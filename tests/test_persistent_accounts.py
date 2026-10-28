"""Accounts persist, and changing one revokes its sessions everywhere at once.

Accounts used to be a dict rebuilt at import, so nothing created at runtime
survived a restart, and there was no way to take access away from one person
short of changing an environment variable and redeploying. They now live in the
`users` table, and every token carries the account's `token_version`: a reset,
a role change, a block -- and an unblock -- increments it, so the commit itself
is the revocation.

Each test below is aimed at one line whose removal must fail it. Where a
negative case could pass for the wrong reason, the docstring says which reason
is excluded: a blocked account's *old* token is refused by the version check
alone, so the block check is tested with a token at the *current* version.

The cross-process and concurrency halves run against PostgreSQL in
tests/test_persistent_accounts_postgres.py.
"""
import os
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app import accounts, auth
from app.database import SessionLocal, User
from app.passwords import _hash_password
from tests._auth_tokens import access_token_for, bearer

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "an entirely different passphrase"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _login(client, username, password=PASSWORD):
    return client.post("/api/v1/auth/login-json", json={"username": username, "password": password})


def _tokens(client, username, password=PASSWORD):
    response = _login(client, username, password)
    assert response.status_code == 200, response.text
    return response.json()


def _me(client, access):
    return client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})


def _refresh(client, refresh):
    return client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})


def _version(user_id) -> int:
    with SessionLocal() as session:
        return int(session.get(User, user_id).token_version)


def _admin(client):
    return bearer("admin")


@pytest.fixture
def isolated_session(tmp_path):
    """A private database holding only `users`, for tests that need to control
    every account in it -- the last administrator, an empty table."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    User.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    try:
        yield Session
    finally:
        engine.dispose()


# ------------------------------------------------------------- persistence


def test_an_account_created_here_is_read_by_another_process(make_account):
    """What the dict could never do: survive the process that created it."""
    user = make_account(prefix="persist")
    code = (
        "import uuid, sys\n"
        "from app.database import SessionLocal\n"
        "from app import accounts\n"
        "with SessionLocal() as s:\n"
        f"    st = accounts.state_by_username(s, {user.username!r})\n"
        "print(st.id, st.role, st.token_version, st.password_hash.startswith('$argon2id$'))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                            env=dict(os.environ), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == [str(user.id), "specialist", "1", "True"]


def test_a_new_process_can_log_in_with_it(client, make_account):
    user = make_account(prefix="relogin")
    tokens = _tokens(client, user.username)
    me = _me(client, tokens["access_token"]).json()
    assert me == {"user_id": str(user.id), "username": user.username, "role": "specialist"}


# ------------------------------------------------------------ administration


def test_an_administrator_creates_an_account_that_can_log_in(client):
    name = f"invitee-{uuid.uuid4().hex[:8]}"
    response = client.post("/api/v1/admin/users", headers=_admin(client),
                           json={"username": name, "password": PASSWORD})
    try:
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["username"] == name and body["role"] == "specialist"
        assert body["is_blocked"] is False
        assert "password" not in body and "password_hash" not in body
        assert "token_version" not in body
        assert PASSWORD not in response.text
        assert _login(client, name).status_code == 200
    finally:
        with SessionLocal() as session:
            session.execute(sa.delete(User).where(User.username == name))
            session.commit()


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_only_an_administrator_may_manage_accounts(client, make_account, role):
    target = make_account(prefix="target")
    calls = [
        ("get", "/api/v1/admin/users", None),
        ("post", "/api/v1/admin/users", {"username": "nope-nope", "password": PASSWORD}),
        ("post", f"/api/v1/admin/users/{target.id}/reset-password", {"password": NEW_PASSWORD}),
        ("patch", f"/api/v1/admin/users/{target.id}", {"is_blocked": True}),
    ]
    for method, path, body in calls:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401, path
        assert getattr(client, method)(path, headers=bearer(role), **kwargs).status_code == 403, (role, path)
    assert _version(target.id) == target.token_version, "a refused call changed the account"


def test_a_specialist_may_not_manage_accounts(client, make_account):
    specialist = make_account(prefix="spec")
    access = _tokens(client, specialist.username)["access_token"]
    response = client.post("/api/v1/admin/users", headers={"Authorization": f"Bearer {access}"},
                           json={"username": "nope-nope", "password": PASSWORD})
    assert response.status_code == 403


def test_a_duplicate_username_is_a_conflict(client, make_account):
    user = make_account(prefix="dup")
    response = client.post("/api/v1/admin/users", headers=_admin(client),
                           json={"username": user.username.upper(), "password": PASSWORD})
    assert response.status_code == 409


@pytest.mark.parametrize("body", [
    {"username": "ok-name", "password": "short"},
    {"username": "ok-name", "password": "x" * 129},
    {"username": "no", "password": PASSWORD},
    {"username": "bad name!", "password": PASSWORD},
    {"username": "ok-name", "password": PASSWORD, "role": "superuser"},
    {"username": "ok-name", "password": PASSWORD, "token_version": 99},
])
def test_invalid_creations_are_refused_and_create_nothing(client, body):
    response = client.post("/api/v1/admin/users", headers=_admin(client), json=body)
    assert response.status_code == 422, response.text
    with SessionLocal() as session:
        assert accounts.state_by_username(session, "ok-name") is None


def test_a_validation_error_does_not_echo_the_password(client):
    secret = "this-must-never-come-back"
    response = client.post("/api/v1/admin/users", headers=_admin(client),
                           json={"username": ["not", "a", "string"], "password": secret,
                                 "unexpected": secret})
    assert response.status_code == 422
    assert secret not in response.text


@pytest.mark.parametrize("field", ["username", "password_hash", "token_version", "id"])
def test_patch_accepts_only_block_and_role(client, make_account, field):
    user = make_account(prefix="patch")
    response = client.patch(f"/api/v1/admin/users/{user.id}", headers=_admin(client),
                            json={field: "x"})
    assert response.status_code == 422
    assert _version(user.id) == user.token_version


def test_list_users_reads_the_table(client, make_account):
    user = make_account(prefix="listed")
    rows = client.get("/api/v1/admin/users", headers=_admin(client)).json()
    mine = [r for r in rows if r["user_id"] == str(user.id)]
    assert mine and mine[0]["username"] == user.username
    assert all("password_hash" not in r and "token_version" not in r for r in rows)


# -------------------------------------------------------------- the refusal


def test_unknown_wrong_and_blocked_get_the_same_answer(client, make_account):
    blocked = make_account(prefix="blk", is_blocked=True)
    active = make_account(prefix="act")
    answers = {
        "unknown": _login(client, "nobody-at-all"),
        "wrong": _login(client, active.username, "wrong password entirely"),
        "blocked": _login(client, blocked.username),
    }
    statuses = {k: v.status_code for k, v in answers.items()}
    assert set(statuses.values()) == {401}, statuses
    assert len({v.text for v in answers.values()}) == 1


def test_form_and_json_login_use_the_same_accounts(client, make_account):
    user = make_account(prefix="form")
    form = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert form.status_code == 200, form.text
    blocked = make_account(prefix="formblk", is_blocked=True)
    assert client.post("/api/v1/auth/login",
                       data={"username": blocked.username, "password": PASSWORD}).status_code == 401


# --------------------------------------------------------------- revocation


def test_a_reset_revokes_existing_access_and_refresh_tokens(client, make_account):
    user = make_account(prefix="reset")
    tokens = _tokens(client, user.username)
    assert _me(client, tokens["access_token"]).status_code == 200

    response = client.post(f"/api/v1/admin/users/{user.id}/reset-password",
                           headers=_admin(client), json={"password": NEW_PASSWORD})
    assert response.status_code == 200, response.text

    assert _me(client, tokens["access_token"]).status_code == 401
    assert _refresh(client, tokens["refresh_token"]).status_code == 401
    assert _login(client, user.username).status_code == 401, "the old password still works"
    assert _login(client, user.username, NEW_PASSWORD).status_code == 200


def test_a_block_revokes_and_an_unblock_does_not_restore(client, make_account):
    user = make_account(prefix="unblk")
    tokens = _tokens(client, user.username)

    assert client.patch(f"/api/v1/admin/users/{user.id}", headers=_admin(client),
                        json={"is_blocked": True}).status_code == 200
    assert _me(client, tokens["access_token"]).status_code == 401
    assert _login(client, user.username).status_code == 401

    assert client.patch(f"/api/v1/admin/users/{user.id}", headers=_admin(client),
                        json={"is_blocked": False}).status_code == 200
    # The sessions held when the block was applied stay dead.
    assert _me(client, tokens["access_token"]).status_code == 401
    assert _refresh(client, tokens["refresh_token"]).status_code == 401
    assert _login(client, user.username).status_code == 200


def test_a_role_change_revokes_and_the_new_role_applies(client, make_account):
    user = make_account(prefix="role")
    tokens = _tokens(client, user.username)
    assert client.patch(f"/api/v1/admin/users/{user.id}", headers=_admin(client),
                        json={"role": "viewer"}).status_code == 200
    assert _me(client, tokens["access_token"]).status_code == 401
    fresh = _tokens(client, user.username)
    assert _me(client, fresh["access_token"]).json()["role"] == "viewer"


def test_a_reset_does_not_unblock(client, make_account):
    user = make_account(prefix="rstblk", is_blocked=True)
    assert client.post(f"/api/v1/admin/users/{user.id}/reset-password", headers=_admin(client),
                       json={"password": NEW_PASSWORD}).status_code == 200
    assert _login(client, user.username, NEW_PASSWORD).status_code == 401
    with SessionLocal() as session:
        assert session.get(User, user.id).is_blocked is True


def test_a_blocked_account_is_refused_even_at_its_current_version(client, make_account):
    """Isolates the block check. A token at the *old* version is refused by the
    version comparison alone, so it proves nothing about `is_blocked`."""
    user = make_account(prefix="curblk", is_blocked=True)
    current = _version(user.id)
    access = access_token_for(user.username, ver=current)
    refresh = auth._jwt_encode(dict(auth._jwt_decode(access), type="refresh", jti="x"))
    assert _me(client, access).status_code == 401
    assert _refresh(client, refresh).status_code == 401


def test_a_stale_version_is_refused_for_an_active_account(client, make_account):
    """Isolates the version check: the account is active, only `ver` is old."""
    user = make_account(prefix="stale")
    with SessionLocal() as session:
        accounts.reset_password(session, user.id, NEW_PASSWORD, hash_password=_hash_password)
    old = access_token_for(user.username, ver=user.token_version)
    assert _me(client, old).status_code == 401
    assert _me(client, access_token_for(user.username)).status_code == 200


def test_the_role_comes_from_the_row_not_the_token(client):
    """A viewer's token claiming admin is still a viewer."""
    forged = access_token_for("viewer", role="admin")
    assert client.get("/api/v1/admin/users",
                      headers={"Authorization": f"Bearer {forged}"}).status_code == 403
    assert _me(client, forged).json()["role"] == "viewer"


def test_refresh_reissues_from_the_row(client, make_account):
    user = make_account(prefix="refrow")
    tokens = _tokens(client, user.username)
    with SessionLocal() as session:
        session.execute(sa.update(User).where(User.id == user.id).values(role="viewer"))
        session.commit()
    # A role written behind the API's back, without a version bump: the
    # refreshed pair must carry the row's role, not the one the token had.
    pair = _refresh(client, tokens["refresh_token"]).json()
    assert auth._jwt_decode(pair["access_token"])["role"] == "viewer"


# ------------------------------------------------------------------- claims


@pytest.mark.parametrize("mutate,why", [
    (lambda p: p.pop("ver"), "no session version"),
    (lambda p: p.pop("exp"), "no expiry"),
    (lambda p: p.pop("sub"), "no subject"),
    (lambda p: p.pop("iat"), "no issue time"),
    (lambda p: p.update(ver="1"), "a string version"),
    (lambda p: p.update(ver=0), "version zero"),
    (lambda p: p.update(ver=True), "a boolean version"),
    (lambda p: p.update(sub="viewer"), "the old username subject"),
    (lambda p: p.update(sub=str(uuid.uuid4())), "an account that does not exist"),
    (lambda p: p.update(type="refresh"), "a refresh token used as access"),
    (lambda p: p.update(exp=1), "expired"),
])
def test_a_token_missing_or_misstating_a_claim_is_refused(client, mutate, why):
    payload = auth._jwt_decode(access_token_for("viewer"))
    mutate(payload)
    token = auth._jwt_encode(payload)
    assert _me(client, token).status_code == 401, why


def test_an_access_token_is_not_a_refresh_token(client):
    assert _refresh(client, access_token_for("viewer")).status_code == 401


def test_a_token_signed_with_another_algorithm_header_is_refused(client):
    token = access_token_for("viewer")
    header, payload, sig = token.split(".")
    none_header = auth._b64url_encode(b'{"alg":"none","typ":"JWT"}')
    assert _me(client, f"{none_header}.{payload}.{sig}").status_code == 401


# ------------------------------------------------------------- the races


def test_a_reset_during_verification_refuses_the_login(monkeypatch, make_account):
    """The old password was checked; a reset committed meanwhile. No tokens."""
    user = make_account(prefix="race")
    real = auth._verify_under_gate

    def verify_then_reset(plain, stored):
        result = real(plain, stored)
        with SessionLocal() as session:
            accounts.reset_password(session, user.id, NEW_PASSWORD, hash_password=_hash_password)
        return result

    monkeypatch.setattr(auth, "_verify_under_gate", verify_then_reset)
    assert auth.authenticate(user.username, PASSWORD) is None


def test_a_block_during_verification_refuses_the_login(monkeypatch, make_account):
    user = make_account(prefix="raceblk")
    real = auth._verify_under_gate

    def verify_then_block(plain, stored):
        result = real(plain, stored)
        with SessionLocal() as session:
            session.execute(sa.update(User).where(User.id == user.id).values(is_blocked=True))
            session.commit()
        return result

    monkeypatch.setattr(auth, "_verify_under_gate", verify_then_block)
    assert auth.authenticate(user.username, PASSWORD) is None


def test_the_version_is_incremented_in_sql_not_from_a_loaded_row(make_account):
    """A session that loaded the row before another reset must not write back a
    version computed from what it loaded: two resets, two increments."""
    user = make_account(prefix="incr")
    with SessionLocal() as stale, SessionLocal() as other:
        stale.get(User, user.id)  # identity map now holds version 1
        accounts.reset_password(other, user.id, NEW_PASSWORD, hash_password=_hash_password)
        accounts.reset_password(stale, user.id, PASSWORD, hash_password=_hash_password)
    assert _version(user.id) == user.token_version + 2


def test_every_access_change_increments_the_version(make_account):
    user = make_account(prefix="every")
    actor = uuid.uuid4()
    with SessionLocal() as session:
        steps = [
            lambda: accounts.update_access(session, actor_id=actor, user_id=user.id, is_blocked=True),
            lambda: accounts.update_access(session, actor_id=actor, user_id=user.id, is_blocked=False),
            lambda: accounts.update_access(session, actor_id=actor, user_id=user.id, role="viewer"),
            lambda: accounts.reset_password(session, user.id, NEW_PASSWORD, hash_password=_hash_password),
        ]
        for n, step in enumerate(steps, start=1):
            step()
            assert _version(user.id) == user.token_version + n, n


# ----------------------------------------------------- administrator safety


def _seed(Session, *rows):
    with Session() as session:
        ids = []
        for name, role in rows:
            user = accounts.create_user(session, username=name, password=PASSWORD, role=role,
                                        created_by=None, hash_password=_hash_password)
            ids.append(user.id)
        return ids


def test_the_last_active_administrator_cannot_be_blocked_or_demoted(isolated_session):
    actor, only = _seed(isolated_session, ("actor-admin", "admin"), ("only-admin", "admin"))
    with isolated_session() as session:
        accounts.update_access(session, actor_id=only, user_id=actor, role="viewer")
        for change in ({"is_blocked": True}, {"role": "specialist"}):
            with pytest.raises(accounts.AccountError) as excinfo:
                accounts.update_access(session, actor_id=uuid.uuid4(), user_id=only, **change)
            assert excinfo.value.status == 409


def test_an_administrator_cannot_block_or_demote_themselves(isolated_session):
    me, _other = _seed(isolated_session, ("self-admin", "admin"), ("other-admin", "admin"))
    with isolated_session() as session:
        for change in ({"is_blocked": True}, {"role": "viewer"}):
            with pytest.raises(accounts.AccountError) as excinfo:
                accounts.update_access(session, actor_id=me, user_id=me, **change)
            assert excinfo.value.status == 409


def test_with_two_administrators_one_may_block_the_other(isolated_session):
    a, b = _seed(isolated_session, ("admin-a", "admin"), ("admin-b", "admin"))
    with isolated_session() as session:
        assert accounts.update_access(session, actor_id=a, user_id=b, is_blocked=True).is_blocked


# ---------------------------------------------------------------- bootstrap


PASSWORDS = {"admin": "first admin pass", "analyst": "first analyst pass",
             "viewer": "first viewer pass"}


def test_bootstrap_creates_the_three_accounts_once(isolated_session):
    with isolated_session() as session:
        assert accounts.bootstrap(session, PASSWORDS, _hash_password) == "created"
        rows = {u.username: u for u in accounts.list_users(session)}
    assert set(rows) == {"admin", "analyst", "viewer"}
    assert {u.role for u in rows.values()} == {"admin", "analyst", "viewer"}


def test_bootstrap_never_rewrites_an_existing_table(isolated_session):
    with isolated_session() as session:
        accounts.bootstrap(session, PASSWORDS, _hash_password)
        viewer = accounts.state_by_username(session, "viewer")
        accounts.reset_password(session, viewer.id, NEW_PASSWORD, hash_password=_hash_password)
        accounts.update_access(session, actor_id=uuid.uuid4(), user_id=viewer.id, is_blocked=True)
        before = {(u.id, u.password_hash, u.role, u.is_blocked, u.token_version)
                  for u in accounts.list_users(session)}

    changed = {role: "a different value now" for role in PASSWORDS}
    with isolated_session() as session:
        assert accounts.bootstrap(session, changed, _hash_password) == "existing"
        after = {(u.id, u.password_hash, u.role, u.is_blocked, u.token_version)
                 for u in accounts.list_users(session)}
    assert after == before


def test_bootstrap_does_not_recreate_a_missing_builtin_account(isolated_session):
    with isolated_session() as session:
        accounts.bootstrap(session, PASSWORDS, _hash_password)
        session.execute(sa.delete(User).where(User.username == "viewer"))
        session.commit()
        assert accounts.bootstrap(session, PASSWORDS, _hash_password) == "existing"
        assert accounts.state_by_username(session, "viewer") is None


def test_bootstrap_refuses_a_table_with_no_active_administrator(isolated_session):
    _seed(isolated_session, ("lonely-viewer", "viewer"))
    with isolated_session() as session:
        with pytest.raises(accounts.AccountError):
            accounts.bootstrap(session, PASSWORDS, _hash_password)


def test_bootstrap_refuses_missing_passwords_and_creates_nothing(isolated_session):
    with isolated_session() as session:
        with pytest.raises(accounts.AccountError):
            accounts.bootstrap(session, dict(PASSWORDS, viewer=""), _hash_password)
        assert accounts.list_users(session) == []


def test_the_script_refuses_dev_passwords_in_production(tmp_path):
    db = tmp_path / "boot.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    User.__table__.create(engine)
    engine.dispose()
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db}", SORA_ENV="production")
    result = subprocess.run([sys.executable, "scripts/bootstrap_users.py", "--dev"],
                            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 1
    assert "refused" in result.stderr


def test_the_script_without_passwords_fails_and_prints_no_secret(tmp_path):
    db = tmp_path / "boot.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    User.__table__.create(engine)
    engine.dispose()
    env = {k: v for k, v in os.environ.items() if not k.startswith("SORA_DEFAULT_")}
    env.update(DATABASE_URL=f"sqlite:///{db}", SORA_ENV="production",
               SORA_DEFAULT_ADMIN_PASSWORD="only the admin one is set")
    result = subprocess.run([sys.executable, "scripts/bootstrap_users.py"],
                            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 1
    assert "analyst" in result.stderr and "viewer" in result.stderr
    assert "only the admin one is set" not in result.stdout + result.stderr


# ------------------------------------------------------------- availability


def test_an_unreadable_account_store_admits_nobody(client, monkeypatch):
    """No fallback to the environment or to memory: 503, and no tokens."""
    token = access_token_for("viewer")

    def unavailable():
        raise sa.exc.OperationalError("SELECT 1", {}, Exception("down"))

    monkeypatch.setattr(auth, "SessionLocal", unavailable)
    assert _me(client, token).status_code == 503
    response = _login(client, "viewer", "viewer123")
    assert response.status_code == 503
    assert "access_token" not in response.text


def test_importing_the_auth_module_touches_no_database():
    code = (
        "import sqlalchemy.engine.base as b\n"
        "calls = []\n"
        "orig = b.Engine.connect\n"
        "b.Engine.connect = lambda self, *a, **k: (calls.append(1), orig(self, *a, **k))[1]\n"
        "import app.auth, app.auth_routes\n"
        "print(len(calls))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=dict(os.environ),
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "0"
