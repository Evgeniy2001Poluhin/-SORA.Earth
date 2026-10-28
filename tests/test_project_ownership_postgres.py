"""Project ownership against PostgreSQL: the migration, the constraints, one
transaction per save, and an audit another process can read.

tests/test_project_ownership.py covers the behaviour on SQLite. What it cannot
show is the part production depends on:

  - that the migration keeps every existing row, with its id and values, and
    puts it in the shared corpus -- on a database that already holds rows, not
    only on an empty one;
  - that a fresh install and a populated upgrade end in the same schema;
  - that the foreign keys exist and RESTRICT, so an account that owns projects
    cannot be deleted, and that the CHECK refuses direct SQL the code would
    never write -- SQLite enforces neither foreign keys nor, here, the server
    default;
  - that a save is one transaction and a failed audit undoes the deletion it
    was recording, as PostgreSQL commits them;
  - that the audit is visible to a different process with its own connection.

Each test gets its own database (tests/postgres_scratch.py); the populated
upgrade migrates in two steps around the rows it seeds.
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app import accounts, project_audit
from app.database import Evaluation, PredictionLog, ProjectAccessEvent
from app.passwords import _hash_password
from app.services import evaluation_store
from tests.postgres_scratch import (REPO_ROOT, _migrate, empty_scratch_database,  # noqa: F401
                                    requires_postgres, scratch_db)

pytestmark = requires_postgres

BEFORE_OWNERSHIP = "a1c5e9f3b7d2"
OWNED = ("evaluations", "predictions_log")


def _user(Session, name, role="specialist"):
    with Session() as session:
        user = accounts.create_user(session, username=name, password="correct horse battery",
                                    role=role, created_by=None, hash_password=_hash_password)
        return accounts.state_by_id(session, user.id)


# --------------------------------------------------- the populated upgrade


SEED_EVALUATIONS = [
    (101, "legacy one", 120000.0, 55.5, datetime(2026, 9, 1, 10, 0)),
    (102, "legacy two", 80000.0, 61.25, datetime(2026, 9, 2, 11, 0)),
    (107, None, 1000.0, 0.0, datetime(2026, 9, 3, 12, 0)),
]
SEED_LOGS = [
    (201, "evaluate", 120000.0, 70.5, "v2.0", datetime(2026, 9, 1, 10, 0)),
    (202, "evaluate", 80000.0, None, "seed:20260901", datetime(2026, 9, 2, 11, 0)),
    (209, "predict_rf", 5.0, 12.0, "unknown", datetime(2026, 9, 3, 12, 0)),
]


def _seeded_upgrade(url):
    """Migrate to the revision before ownership, write rows as that code did
    -- no owner, no scope, those columns do not exist yet -- then upgrade."""
    _migrate(url, BEFORE_OWNERSHIP)
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        for row in SEED_EVALUATIONS:
            conn.execute(sa.text(
                "INSERT INTO evaluations (id, name, budget, total_score, created_at) "
                "VALUES (:i, :n, :b, :s, :c)"), dict(zip("inbsc", row)))
        for row in SEED_LOGS:
            conn.execute(sa.text(
                "INSERT INTO predictions_log (id, endpoint, budget, probability, model_version, "
                "timestamp) VALUES (:i, :e, :b, :p, :m, :t)"), dict(zip("iebpmt", row)))
    engine.dispose()
    _migrate(url, "head")
    return sa.create_engine(url)


def test_the_upgrade_keeps_every_row_and_puts_it_in_the_shared_corpus():
    with empty_scratch_database() as url:
        engine = _seeded_upgrade(url)
        try:
            with engine.connect() as conn:
                evaluations = conn.execute(sa.text(
                    "SELECT id, name, budget, total_score, created_at, owner_user_id, data_scope "
                    "FROM evaluations ORDER BY id")).all()
                logs = conn.execute(sa.text(
                    "SELECT id, endpoint, budget, probability, model_version, timestamp, "
                    "owner_user_id, data_scope FROM predictions_log ORDER BY id")).all()
        finally:
            engine.dispose()
    assert [tuple(r) for r in evaluations] == [row + (None, "public") for row in SEED_EVALUATIONS]
    assert [tuple(r) for r in logs] == [row + (None, "public") for row in SEED_LOGS]


def _schema(engine):
    inspector = sa.inspect(engine)
    out = {}
    for table in OWNED + ("project_access_events", "project_access_targets"):
        out[table] = {
            "columns": sorted((c["name"], str(c["type"]), c["nullable"], str(c.get("default")))
                              for c in inspector.get_columns(table)),
            "checks": sorted((c["name"], c["sqltext"]) for c in inspector.get_check_constraints(table)),
            "fks": sorted((f["name"], tuple(f["constrained_columns"]), f["referred_table"],
                           f["options"].get("ondelete")) for f in inspector.get_foreign_keys(table)),
        }
    with engine.connect() as conn:
        out["indexes"] = sorted(conn.execute(sa.text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = ANY(:t)"),
            {"t": list(out)}).all())
    return out


def test_a_fresh_install_and_a_populated_upgrade_end_in_the_same_schema(scratch_db):
    fresh, _url = scratch_db
    with empty_scratch_database() as url:
        upgraded = _seeded_upgrade(url)
        try:
            assert _schema(upgraded) == _schema(fresh)
        finally:
            upgraded.dispose()


# ------------------------------------------------------- what the schema is


def test_the_constraints_defaults_and_indexes_are_there(scratch_db):
    engine, _url = scratch_db
    schema = _schema(engine)
    for table in OWNED:
        assert (f"ck_{table}_owner_scope",) == tuple(n for n, _ in schema[table]["checks"])
        assert schema[table]["fks"] == [
            (f"fk_{table}_owner_user", ("owner_user_id",), "users", "RESTRICT")]
        scope = [c for c in schema[table]["columns"] if c[0] == "data_scope"]
        assert scope == [("data_scope", "VARCHAR(16)", False, "'pilot'::character varying")]
    defs = dict(schema["indexes"])
    assert "(owner_user_id, created_at DESC, id DESC)" in defs["ix_evaluations_owner_recent"]
    assert "(data_scope, created_at DESC, id DESC)" in defs["ix_evaluations_scope_recent"]
    assert "(owner_user_id, \"timestamp\" DESC, id DESC)" in defs["ix_predictions_log_owner_recent"]
    assert "(data_scope, \"timestamp\" DESC, id DESC)" in defs["ix_predictions_log_scope_recent"]
    assert "(data_scope, id DESC)" in defs["ix_predictions_log_scope_id"]
    assert "(occurred_at DESC, id)" in defs["ix_project_access_events_recent"]
    assert "(resource_type, resource_id, event_id)" in defs["ix_project_access_targets_resource"]


@pytest.mark.parametrize("table", OWNED)
@pytest.mark.parametrize("case", ["unknown owner", "unknown scope", "pilot without owner",
                                  "public with owner", "nothing said"])
def test_direct_sql_cannot_write_what_the_code_never_would(scratch_db, table, case):
    engine, _url = scratch_db
    Session = sessionmaker(bind=engine)
    real = _user(Session, f"real-{uuid.uuid4().hex[:6]}").id
    values = {
        "unknown owner": "owner_user_id = :stranger, data_scope = 'pilot'",
        "unknown scope": "owner_user_id = NULL, data_scope = 'shared'",
        "pilot without owner": "owner_user_id = NULL, data_scope = 'pilot'",
        "public with owner": "owner_user_id = :real, data_scope = 'public'",
        "nothing said": None,
    }[case]
    if values is None:
        sql = f"INSERT INTO {table} (budget) VALUES (1)"
    else:
        cols, vals = zip(*(part.split(" = ") for part in values.split(", ")))
        sql = f"INSERT INTO {table} (budget, {', '.join(cols)}) VALUES (1, {', '.join(vals)})"
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(sql), {"stranger": uuid.uuid4(), "real": real})


def test_the_pairs_the_code_writes_are_accepted(scratch_db):
    """The control for the case above: the refusals are the constraint, not a
    statement that could never succeed."""
    engine, _url = scratch_db
    owner = _user(sessionmaker(bind=engine), "accepted-owner").id
    with engine.begin() as conn:
        for table in OWNED:
            conn.execute(sa.text(f"INSERT INTO {table} (budget, data_scope) VALUES (1, 'public')"))
            conn.execute(sa.text(f"INSERT INTO {table} (budget, owner_user_id) VALUES (1, :o)"),
                         {"o": owner})
        rows = conn.execute(sa.text("SELECT data_scope FROM evaluations ORDER BY id")).scalars().all()
    assert rows == ["public", "pilot"]


@pytest.mark.parametrize("what", ["evaluation", "log row", "audit event"])
def test_an_account_that_owns_something_cannot_be_deleted(scratch_db, what):
    engine, _url = scratch_db
    Session = sessionmaker(bind=engine)
    owner = _user(Session, f"owner-{uuid.uuid4().hex[:6]}", role="admin")
    with Session() as s:
        if what == "evaluation":
            s.add(Evaluation(budget=1.0, owner_user_id=owner.id, data_scope="pilot"))
        elif what == "log row":
            s.add(PredictionLog(budget=1.0, owner_user_id=owner.id, data_scope="pilot"))
        else:
            project_audit.record(s, actor=SimpleNamespace(user_id=str(owner.id), username="x",
                                                          role="admin"),
                                 request=_request(), action="list", status_code=200)
        s.commit()
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM users WHERE id = :i"), {"i": owner.id})


# ----------------------------------------------------- one transaction


def _request():
    return SimpleNamespace(method="GET", scope={}, url=SimpleNamespace(path="/api/v1/history"))


def _project():
    return SimpleNamespace(name="pg save", budget=100000.0, co2_reduction=50.0, social_impact=7.0,
                           duration_months=24, category="Solar Energy", region="Germany")


RESULT = {"total_score": 60.0, "environment_score": 60.0, "social_score": 60.0,
          "economic_score": 60.0, "success_probability": 71.0, "risk_level": "Medium"}


def _save(engine, owner_id):
    return evaluation_store.save_evaluation(
        owner_user_id=owner_id, project=_project(), result=RESULT, recommendation="r",
        region_name="Europe", lat=50.0, lon=10.0, latency_ms=3.0, model_version="unknown",
        threshold=0.5, session_factory=sessionmaker(bind=engine))


def test_a_save_is_one_commit_holding_both_rows(scratch_db):
    engine, _url = scratch_db
    owner = _user(sessionmaker(bind=engine), "one-commit").id
    commits = []

    def count(conn):
        commits.append(1)

    sa.event.listen(engine, "commit", count)
    try:
        evaluation_id = _save(engine, owner)
    finally:
        sa.event.remove(engine, "commit", count)
    with engine.connect() as conn:
        ev = conn.execute(sa.text("SELECT owner_user_id, data_scope FROM evaluations WHERE id = :i"),
                          {"i": evaluation_id}).one()
        logs = conn.execute(sa.text("SELECT owner_user_id, data_scope FROM predictions_log")).all()
    assert len(commits) == 1
    assert tuple(ev) == (owner, "pilot")
    assert [tuple(r) for r in logs] == [(owner, "pilot")]


def test_a_failed_second_row_leaves_nothing(scratch_db, monkeypatch):
    engine, _url = scratch_db
    owner = _user(sessionmaker(bind=engine), "half-save").id
    real = evaluation_store.prediction_log_row

    def broken(**kwargs):
        row = real(**kwargs)
        row.owner_user_id = uuid.uuid4()   # no such account: the FK refuses it
        return row

    monkeypatch.setattr(evaluation_store, "prediction_log_row", broken)
    with pytest.raises(sa.exc.IntegrityError):
        _save(engine, owner)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM evaluations")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM predictions_log")).scalar_one() == 0


# ------------------------------------------------------------- the audit


def _admin(Session, name="audit-admin"):
    state = _user(Session, name, role="admin")
    return SimpleNamespace(user_id=str(state.id), username=state.username, role="admin")


CHILD = r"""
import sys
from app.database import SessionLocal, ProjectAccessEvent, ProjectAccessTarget
with SessionLocal() as s:
    e = s.query(ProjectAccessEvent).one()
    ids = sorted(t.resource_id for t in s.query(ProjectAccessTarget).filter_by(event_id=e.id))
print(e.action, e.actor_username, ",".join(map(str, ids)))
"""


def test_the_audit_is_read_by_another_process(scratch_db):
    engine, url = scratch_db
    Session = sessionmaker(bind=engine)
    actor = _admin(Session)
    with Session() as s:
        rows = [Evaluation(budget=1.0, data_scope="public") for _ in range(2)]
        s.add_all(rows)
        s.flush()
        project_audit.record(s, actor=actor, request=_request(), action="export", status_code=200,
                             targets=[project_audit.target("evaluation", r, effect="returned")
                                      for r in rows])
        project_audit.commit_or_refuse(s)
        ids = sorted(r.id for r in rows)

    env = dict(os.environ, DATABASE_URL=url.render_as_string(hide_password=False))
    out = subprocess.run([sys.executable, "-c", CHILD], cwd=REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split() == ["export", actor.username, ",".join(map(str, ids))]


def test_an_audit_that_cannot_commit_is_a_503_and_undoes_the_deletion(scratch_db):
    """The deletion and its record are one transaction: when the record fails
    -- here an actor id no account has, which the foreign key refuses -- the
    DELETE beside it is rolled back too, as seen from another connection."""
    engine, _url = scratch_db
    Session = sessionmaker(bind=engine)
    with Session() as s:
        row = Evaluation(budget=1.0, data_scope="public")
        s.add(row)
        s.commit()
        row_id = row.id

    ghost = SimpleNamespace(user_id=str(uuid.uuid4()), username="ghost", role="admin")
    with Session() as s:
        victim = s.get(Evaluation, row_id)
        project_audit.record(s, actor=ghost, request=_request(), action="delete", status_code=200,
                             targets=[project_audit.target("evaluation", victim, effect="deleted")])
        s.query(Evaluation).filter(Evaluation.id == row_id).delete(synchronize_session=False)
        with pytest.raises(HTTPException) as refused:
            project_audit.commit_or_refuse(s)
    assert refused.value.status_code == 503
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM evaluations WHERE id = :i"),
                            {"i": row_id}).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM project_access_events")).scalar_one() == 0


# -------------------------------------------- PostgreSQL-only shared readers


def test_the_lstm_volume_counts_public_days_only(scratch_db):
    """`/forecast/lstm-status` counts recent days in raw SQL that SQLite cannot
    run (`::date`, `INTERVAL`), so it is checked here, on the text it executes."""
    from app.api.forecast import RECENT_VOLUME_SQL

    engine, _url = scratch_db
    owner = _user(sessionmaker(bind=engine), "volume-owner").id
    now = datetime.utcnow()
    with engine.begin() as conn:
        for d in (1, 2):
            conn.execute(sa.text("INSERT INTO evaluations (budget, created_at, data_scope) "
                                 "VALUES (1, :c, 'public')"), {"c": now - timedelta(days=d)})
        for d in range(3, 9):
            conn.execute(sa.text("INSERT INTO evaluations (budget, created_at, owner_user_id) "
                                 "VALUES (1, :c, :o)"), {"c": now - timedelta(days=d), "o": owner})
        unique_days, _last = conn.execute(sa.text(RECENT_VOLUME_SQL)).one()
    assert unique_days == 2
