"""Session tokens for tests, issued the way login issues them.

A token names an account by its UUID and carries its session version, so a
test can no longer write `create_access_token({"sub": "viewer"})`: that payload
names nobody and is refused. These look the account up and mint a token for
it exactly as the login route does.

`override` replaces claims after signing input is assembled, for the tests
whose point is a forged or stale claim -- `role="admin"` on a viewer, an old
`ver`. The result is still validly signed, which is what makes those tests
meaningful: the refusal has to come from the claim check, not the signature.
"""
from app import accounts, auth
from app.database import SessionLocal


def account(username: str) -> accounts.AccountState:
    with SessionLocal() as session:
        state = accounts.state_by_username(session, username)
    assert state is not None, f"no account {username!r} in the test database"
    return state


def token_for(username: str, kind: str = "access", **override) -> str:
    state = account(username)
    token = auth.create_access_token(state) if kind == "access" else auth.create_refresh_token(state)
    if not override:
        return token
    payload = auth._jwt_decode(token)
    payload.update(override)
    return auth._jwt_encode(payload)


def access_token_for(username: str, **override) -> str:
    return token_for(username, "access", **override)


def refresh_token_for(username: str, **override) -> str:
    return token_for(username, "refresh", **override)


def bearer(username: str, **override) -> dict:
    return {"Authorization": f"Bearer {access_token_for(username, **override)}"}
