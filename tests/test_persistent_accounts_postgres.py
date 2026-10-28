"""Accounts against PostgreSQL: the schema, separate processes, and concurrency.

tests/test_persistent_accounts.py covers behaviour on whatever database the
suite has. What it cannot show is the part that matters most in production:

  - that a process with its own PID and its own connection pool refuses a
    token the moment another process commits a reset -- two TestClients in one
    process share an engine, and would pass with a process-local cache;
  - that two concurrent changes cannot both win: two resets are two
    increments, and two administrators blocking each other leave one standing;
  - that the migration builds what the models declare, constraints included.

Each test gets a fresh database migrated to head (tests/postgres_scratch.py).
"""
import os
import subprocess
import sys
import threading
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app import accounts, auth
from app.database import User
from app.passwords import _hash_password
from tests.postgres_scratch import REPO_ROOT, requires_postgres, scratch_db  # noqa: F401

pytestmark = requires_postgres

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "an entirely different passphrase"


def _make(Session, name, role="specialist"):
    with Session() as session:
        user = accounts.create_user(session, username=name, password=PASSWORD, role=role,
                                    created_by=None, hash_password=_hash_password)
        return accounts.state_by_id(session, user.id)


class _Blocked:
    """Hold a lock in a separate transaction, start the calls, prove they wait.

    A barrier only aligns when two threads *start*; their SQL can still run one
    after the other, and then a test of a race passes without a race. Here an
    outside transaction holds the lock the code is supposed to queue behind.
    The calls are started, shown to be still running after a pause -- so they
    really are waiting on that lock and will overlap once it goes -- and only
    then is it released.
    """

    def __init__(self, engine, sql, params=None):
        self.engine, self.sql, self.params = engine, sql, params or {}

    def run(self, *calls, pause=1.0):
        conn = self.engine.connect()
        tx = conn.begin()
        conn.execute(sa.text(self.sql), self.params)
        results = [None] * len(calls)

        def run(i, call):
            try:
                results[i] = call()
            except Exception as exc:  # the outcome is the assertion, not a crash
                results[i] = exc

        threads = [threading.Thread(target=run, args=(i, c), daemon=True) for i, c in enumerate(calls)]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=pause)
            waiting = [t.is_alive() for t in threads]
        finally:
            tx.rollback()
            conn.close()
        for t in threads:
            t.join(timeout=60)
        assert not any(t.is_alive() for t in threads), "a call never finished"
        return waiting, results


# ------------------------------------------------------------------ schema


def test_the_migration_builds_what_the_model_declares(scratch_db):
    engine, _url = scratch_db
    inspector = sa.inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("users")}

    assert set(columns) == set(User.__table__.columns.keys())
    for name, column in User.__table__.columns.items():
        assert columns[name]["nullable"] == column.nullable, name
    assert isinstance(columns["id"]["type"], sa.Uuid)
    assert isinstance(columns["created_by_user_id"]["type"], sa.Uuid)
    assert columns["created_at"]["type"].timezone is True

    checks = {c["name"] for c in inspector.get_check_constraints("users")}
    assert checks == {"ck_users_username", "ck_users_role", "ck_users_token_version",
                      "ck_users_password_argon2id"}
    assert {u["name"] for u in inspector.get_unique_constraints("users")} == {"uq_users_username"}
    assert inspector.get_pk_constraint("users")["name"] == "pk_users"
    fks = inspector.get_foreign_keys("users")
    assert [(f["name"], f["referred_table"], f["options"].get("ondelete")) for f in fks] == [
        ("fk_users_created_by", "users", "RESTRICT")]


@pytest.mark.parametrize("column,value", [
    ("role", "superuser"),
    ("username", "Mixed-Case"),
    ("username", "ab"),
    ("password_hash", "a" * 64),
    ("token_version", 0),
])
def test_the_table_refuses_what_the_code_would_never_write(scratch_db, column, value):
    engine, _url = scratch_db
    row = {"id": uuid.uuid4(), "username": "valid-name", "role": "viewer",
           "password_hash": _hash_password(PASSWORD)}
    row[column] = value
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.insert(User.__table__).values(**row))


#: Every foreign key that points at `users`, and what deleting a referenced
#: account does. The accounts migration linked nothing; project ownership
#: (b7d41f2e9c35) added the three below. RESTRICT throughout: an account with
#: saved evaluations or recorded access cannot be deleted, only blocked --
#: SET NULL would erase whose a row was, CASCADE would delete it.
EXPECTED_REFERENCES_TO_USERS = {
    ("evaluations", "fk_evaluations_owner_user", ("owner_user_id",), "RESTRICT"),
    ("predictions_log", "fk_predictions_log_owner_user", ("owner_user_id",), "RESTRICT"),
    ("project_access_events", "fk_project_access_events_actor", ("actor_user_id",), "RESTRICT"),
}


def test_only_the_declared_tables_point_at_users(scratch_db):
    """No table gains a column pointing at users without being named above."""
    engine, _url = scratch_db
    inspector = sa.inspect(engine)
    referencing = {
        (t, f["name"], tuple(f["constrained_columns"]), f["options"].get("ondelete"))
        for t in inspector.get_table_names() if t != "users"
        for f in inspector.get_foreign_keys(t) if f["referred_table"] == "users"
    }
    assert referencing == EXPECTED_REFERENCES_TO_USERS


# --------------------------------------------------------- another process


CHILD = r"""
import sys
from app import auth
for line in sys.stdin:
    token = line.strip()
    try:
        auth.account_for_token(auth._jwt_decode(token), "access")
        print("accepted", flush=True)
    except ValueError as exc:
        print("refused", flush=True)
"""


@pytest.mark.parametrize("change", ["reset", "block"])
def test_another_process_refuses_the_token_once_the_change_commits(scratch_db, change):
    engine, url = scratch_db
    Session = sessionmaker(bind=engine)
    user = _make(Session, f"proc-{change}")
    token = auth.create_access_token(user)

    env = dict(os.environ, DATABASE_URL=url.render_as_string(hide_password=False))
    child = subprocess.Popen([sys.executable, "-c", CHILD], cwd=REPO_ROOT, env=env, text=True,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        def ask():
            child.stdin.write(token + "\n")
            child.stdin.flush()
            return child.stdout.readline().strip()

        # The first answer warms the child's pool, so the second cannot be
        # explained by a connection opened after the change.
        assert ask() == "accepted"
        assert child.pid != os.getpid()

        with Session() as session:
            if change == "reset":
                accounts.reset_password(session, user.id, NEW_PASSWORD, hash_password=_hash_password)
            else:
                accounts.update_access(session, actor_id=uuid.uuid4(), user_id=user.id,
                                       is_blocked=True)

        assert ask() == "refused"
    finally:
        child.stdin.close()
        child.wait(timeout=60)
        if child.returncode not in (0, None):
            pytest.fail(child.stderr.read()[-2000:])


# ------------------------------------------------------------ concurrency


def test_two_resets_queued_on_one_row_are_two_increments(scratch_db):
    """Both resets wait on the row; both then write. A version computed in
    Python from a read taken before the wait would give +1, not +2."""
    engine, _url = scratch_db
    Session = sessionmaker(bind=engine)
    user = _make(Session, "twice-reset")

    def reset(password):
        with Session() as session:
            return accounts.reset_password(session, user.id, password, hash_password=_hash_password)

    held = _Blocked(engine, "SELECT 1 FROM users WHERE id = :id FOR UPDATE", {"id": user.id})
    waiting, results = held.run(lambda: reset(NEW_PASSWORD), lambda: reset(PASSWORD + " again"))
    assert waiting == [True, True], "the resets did not wait on the row, so nothing overlapped"
    assert not [r for r in results if isinstance(r, Exception)], results
    with Session() as session:
        assert session.get(User, user.id).token_version == user.token_version + 2


ACCESS_LOCK = "SELECT pg_advisory_xact_lock(hashtext('sora.users.access'))"


def test_access_changes_queue_behind_one_lock(scratch_db):
    """Every block, unblock and role change takes the same transaction lock
    before reading anything, so two of them never interleave -- which is what
    rules out both "each left the other standing" and a lock-order deadlock."""
    engine, _url = scratch_db
    Session = sessionmaker(bind=engine)
    target = _make(Session, "queued-target")

    def block():
        with Session() as session:
            return accounts.update_access(session, actor_id=uuid.uuid4(), user_id=target.id,
                                          is_blocked=True)

    waiting, results = _Blocked(engine, ACCESS_LOCK).run(block)
    assert waiting == [True], "update_access did not wait for the access lock"
    assert not isinstance(results[0], Exception), results


def test_two_administrators_blocking_each_other_leave_one_standing(scratch_db):
    engine, _url = scratch_db
    Session = sessionmaker(bind=engine)
    a = _make(Session, "admin-left", role="admin")
    b = _make(Session, "admin-right", role="admin")

    def block(actor, target):
        with Session() as session:
            return accounts.update_access(session, actor_id=actor, user_id=target, is_blocked=True)

    waiting, results = _Blocked(engine, ACCESS_LOCK).run(
        lambda: block(a.id, b.id), lambda: block(b.id, a.id))
    assert waiting == [True, True], "the two changes did not overlap"
    refused = [r for r in results if isinstance(r, accounts.AccountError)]
    other = [r for r in results if isinstance(r, Exception) and not isinstance(r, accounts.AccountError)]
    assert other == [], f"a concurrent change failed for a reason other than the guard: {other}"
    assert len(refused) == 1 and refused[0].status == 409

    with Session() as session:
        active = session.execute(sa.select(sa.func.count()).select_from(User).where(
            User.role == "admin", User.is_blocked.is_(False))).scalar_one()
    assert active == 1


def test_a_promotion_racing_a_demotion_neither_deadlocks_nor_strands(scratch_db):
    """The interleaving review found: B is promoted while A, the only other
    administrator, is being demoted. Row locks taken in different orders
    deadlock here; the access lock makes it sequential, whichever goes first."""
    engine, _url = scratch_db
    Session = sessionmaker(bind=engine)
    actor = _make(Session, "admin-actor", role="admin")
    a = _make(Session, "admin-only", role="admin")
    b = _make(Session, "soon-admin", role="viewer")
    with Session() as session:
        accounts.update_access(session, actor_id=a.id, user_id=actor.id, role="viewer")

    def promote():
        with Session() as session:
            return accounts.update_access(session, actor_id=a.id, user_id=b.id, role="admin")

    def demote():
        with Session() as session:
            return accounts.update_access(session, actor_id=b.id, user_id=a.id, role="viewer")

    waiting, results = _Blocked(engine, ACCESS_LOCK).run(promote, demote)
    assert waiting == [True, True]
    unexpected = [r for r in results if isinstance(r, Exception)
                  and not isinstance(r, accounts.AccountError)]
    assert unexpected == [], f"deadlock or other failure: {unexpected}"
    with Session() as session:
        active = session.execute(sa.select(sa.func.count()).select_from(User).where(
            User.role == "admin", User.is_blocked.is_(False))).scalar_one()
    assert active >= 1


def test_two_queued_bootstraps_create_the_accounts_once(scratch_db):
    engine, _url = scratch_db
    Session = sessionmaker(bind=engine)
    passwords = {"admin": "first admin pass", "analyst": "first analyst pass",
                 "viewer": "first viewer pass"}

    def boot():
        with Session() as session:
            return accounts.bootstrap(session, passwords, _hash_password)

    held = _Blocked(engine, "SELECT pg_advisory_xact_lock(hashtext('sora.users.bootstrap'))")
    waiting, results = held.run(boot, boot)
    assert waiting == [True, True], "the bootstraps did not wait for the bootstrap lock"
    assert sorted(results) == ["created", "existing"], results
    with Session() as session:
        assert sorted(u.username for u in accounts.list_users(session)) == ["admin", "analyst", "viewer"]
