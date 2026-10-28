"""Accounts, stored in PostgreSQL (app/database.py:User).

Every operation that changes who may do what -- a password reset, a role
change, blocking, unblocking -- increments `token_version` in the same UPDATE,
as `token_version = token_version + 1` in SQL rather than a value computed in
Python, so two concurrent changes cannot both write "version 2" and leave one
of them unrevoked. app/auth.py accepts a token only while its `ver` equals the
row's current version, so the commit is the revocation, in every worker at once.

Nothing here caches. A session is opened for each operation and closed after
it, so no identity map outlives the request and serves a row from before a
reset.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import USER_ROLES, User

#: The three accounts the platform has always had. Created once, by
#: scripts/bootstrap_users.py, from SORA_DEFAULT_<ROLE>_PASSWORD.
BOOTSTRAP_ACCOUNTS = ("admin", "analyst", "viewer")

#: Development passwords, used only by an explicit `--dev` bootstrap outside
#: production. They are published in this repository and must never reach a
#: deployed database, which is why the production path refuses to fall back.
DEV_PASSWORDS = {"admin": "sora2026", "analyst": "analyst123", "viewer": "viewer123"}

USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,49}")

#: For passwords set through the API. Long rather than complex, with no MFA
#: behind it; the bootstrap passwords predate this and are not held to it.
PASSWORD_MIN = 15
PASSWORD_MAX = 128


class AccountError(Exception):
    """A refusal the caller should report, with an HTTP status attached."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class AccountState:
    """What a login or a token check needs, read in one statement."""

    id: uuid.UUID
    username: str
    role: str
    is_blocked: bool
    token_version: int
    password_hash: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_username(raw: str) -> str:
    """The one normalisation, applied identically at creation and at login."""
    return (raw or "").strip().lower()


def validate_username(raw: str) -> str:
    name = normalize_username(raw)
    if not USERNAME_PATTERN.fullmatch(name):
        raise AccountError(422, "username must be 3-50 characters of a-z, 0-9, '_', '.', '-', "
                                "starting with a letter or digit")
    return name


def validate_new_password(password: str) -> str:
    # No truncation and no normalisation: what is checked is what is hashed.
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AccountError(422, f"password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters")
    return password


def validate_role(role: str) -> str:
    if role not in USER_ROLES:
        raise AccountError(422, f"role must be one of {', '.join(USER_ROLES)}")
    return role


def _state(user: Optional[User]) -> Optional[AccountState]:
    if user is None:
        return None
    return AccountState(
        id=user.id, username=user.username, role=user.role,
        is_blocked=bool(user.is_blocked), token_version=int(user.token_version),
        password_hash=user.password_hash,
    )


def state_by_username(session: Session, username: str) -> Optional[AccountState]:
    name = normalize_username(username)
    return _state(session.execute(sa.select(User).where(User.username == name)).scalar_one_or_none())


def state_by_id(session: Session, user_id: uuid.UUID) -> Optional[AccountState]:
    return _state(session.get(User, user_id))


def public_view(user: User) -> dict:
    """What an API response may say about an account. Never the hash or version."""
    return {
        "user_id": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_blocked": bool(user.is_blocked),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "password_changed_at": (user.password_changed_at.isoformat()
                                if user.password_changed_at else None),
    }


def create_user(session: Session, *, username: str, password: str, role: str,
                created_by: Optional[uuid.UUID], hash_password: Callable[[str], str]) -> User:
    """Insert and commit. The caller answers 201 only after this returns."""
    name = validate_username(username)
    validate_new_password(password)
    validate_role(role)
    now = _now()
    user = User(
        id=uuid.uuid4(), username=name, password_hash=hash_password(password), role=role,
        is_blocked=False, token_version=1, created_at=now, updated_at=now,
        password_changed_at=now, created_by_user_id=created_by,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if state_by_username(session, name) is not None:
            raise AccountError(409, "username already exists") from None
        raise
    session.refresh(user)
    return user


def reset_password(session: Session, user_id: uuid.UUID, password: str,
                   hash_password: Callable[[str], str]) -> User:
    """New hash and a new session generation, in one statement.

    Blocking is left as it was: a reset is not a way to unblock an account.
    """
    validate_new_password(password)
    now = _now()
    result = session.execute(
        sa.update(User).where(User.id == user_id).values(
            password_hash=hash_password(password),
            token_version=User.token_version + 1,
            password_changed_at=now, updated_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise AccountError(404, "user not found")
    session.commit()
    return session.get(User, user_id)


def _serialise(session: Session, name: str) -> None:
    """A PostgreSQL advisory lock held until this transaction ends.

    SQLite has no such lock and needs none: it admits one writer at a time.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": name})


def _active_admin_ids(session: Session) -> list:
    # FOR UPDATE on every active admin row serialises two concurrent changes
    # that could each leave "another admin" standing: the second waits, and on
    # PostgreSQL re-evaluates the WHERE against the first one's committed row.
    # Ordered, so every caller takes these locks in the same sequence. SQLite
    # ignores the clause and serialises writers on its own.
    return list(session.execute(
        sa.select(User.id)
        .where(User.role == "admin", User.is_blocked.is_(False))
        .order_by(User.id)
        .with_for_update()
    ).scalars())


def update_access(session: Session, *, actor_id: uuid.UUID, user_id: uuid.UUID,
                  is_blocked: Optional[bool] = None, role: Optional[str] = None) -> User:
    """Block, unblock or change role. Any of them starts a new session generation.

    Unblocking increments the version too, so the sessions held when the block
    was applied do not come back to life with it.
    """
    if is_blocked is None and role is None:
        raise AccountError(422, "nothing to change: give is_blocked and/or role")
    if role is not None:
        validate_role(role)

    # One transaction-scoped lock for every access change, taken before any
    # row is read. Row locks alone are not enough: a change that promotes an
    # account adds a row to the administrator set while another transaction is
    # already locking that set, and two transactions taking overlapping row
    # sets in different orders deadlock (measured by review, not hypothetical).
    # Serialising the whole operation removes the ordering question.
    _serialise(session, "sora.users.access")
    admins = _active_admin_ids(session)
    target = session.execute(
        sa.select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    if target is None:
        session.rollback()
        raise AccountError(404, "user not found")

    removes_admin = target.role == "admin" and not target.is_blocked and (
        is_blocked is True or (role is not None and role != "admin"))
    if removes_admin:
        if target.id == actor_id:
            session.rollback()
            raise AccountError(409, "an administrator cannot block or demote their own account")
        if [i for i in admins if i != target.id] == []:
            session.rollback()
            raise AccountError(409, "this is the last active administrator")

    values = {"token_version": User.token_version + 1, "updated_at": _now()}
    if is_blocked is not None:
        values["is_blocked"] = is_blocked
    if role is not None:
        values["role"] = role
    session.execute(sa.update(User).where(User.id == user_id).values(**values))
    session.commit()
    return session.get(User, user_id)


def store_rehash(session: Session, state: AccountState, new_hash: str) -> bool:
    """Persist a re-hash of an unchanged password -- only if nothing moved since.

    Conditional on the hash and the version the login verified against, so a
    reset that committed while the old password was being checked is never
    overwritten with a hash of the old password. A re-hash is the same
    password, so it does not start a new session generation.
    """
    result = session.execute(
        sa.update(User)
        .where(User.id == state.id,
               User.password_hash == state.password_hash,
               User.token_version == state.token_version)
        .values(password_hash=new_hash)
    )
    session.commit()
    return result.rowcount == 1


def list_users(session: Session) -> list:
    return list(session.execute(sa.select(User).order_by(User.created_at, User.username)).scalars())


def bootstrap(session: Session, passwords: dict, hash_password: Callable[[str], str]) -> str:
    """Create the three built-in accounts in an empty table, once.

    Returns "created" or "existing". A table that already holds any account is
    left exactly as it is -- no password, role, block or version is touched --
    so changing SORA_DEFAULT_*_PASSWORD after the first deployment changes
    nothing, and a missing built-in account is not quietly recreated. It then
    still has to contain an active administrator, or the deployment stops.
    """
    # One bootstrap at a time; a second waits and then finds the rows.
    _serialise(session, "sora.users.bootstrap")

    existing = session.execute(sa.select(sa.func.count()).select_from(User)).scalar_one()
    if existing:
        admins = session.execute(
            sa.select(sa.func.count()).select_from(User)
            .where(User.role == "admin", User.is_blocked.is_(False))
        ).scalar_one()
        session.rollback()
        if not admins:
            raise AccountError(409, "accounts exist but none is an active administrator")
        return "existing"

    missing = [role for role in BOOTSTRAP_ACCOUNTS if not passwords.get(role)]
    if missing:
        session.rollback()
        raise AccountError(422, "bootstrap passwords missing for: " + ", ".join(missing))

    now = _now()
    for role in BOOTSTRAP_ACCOUNTS:
        session.add(User(
            id=uuid.uuid4(), username=role, password_hash=hash_password(passwords[role]),
            role=role, is_blocked=False, token_version=1, created_at=now, updated_at=now,
            password_changed_at=now, created_by_user_id=None,
        ))
    try:
        session.commit()
    except IntegrityError:
        # Another bootstrap won on a database without the advisory lock.
        session.rollback()
        return "existing"
    return "created"
