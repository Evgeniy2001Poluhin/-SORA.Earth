"""
SORA.Earth JWT Authentication + RBAC + API Keys
Pure Python JWT (HS256) — no external deps
"""
import hashlib, hmac, json, logging, os, secrets, time, base64, uuid
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher

from app.rate_limit import RateLimiter
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
import threading

from fastapi import Depends, HTTPException, Security, Header, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app import accounts
from app.accounts import AccountState
from app.database import SessionLocal

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SORA_JWT_SECRET", "sora-earth-dev-secret-change-in-production-2026")
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Production security: fail-fast if using dev secrets in production
SORA_ENV = os.getenv("SORA_ENV", "development")
if SORA_ENV == "production":
    if SECRET_KEY.startswith("sora-earth-dev-"):
        raise RuntimeError(
            "CRITICAL: Production deployment detected with development JWT secret! "
            "Set SORA_JWT_SECRET environment variable to a secure random value."
        )

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60

class LoginRequest(BaseModel):
    username: str
    password: str

class UserInfo(BaseModel):
    user_id: str
    username: str
    role: str

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)

def _jwt_encode(payload: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(SECRET_KEY.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"

def _jwt_decode(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")
    h, p, s = parts
    expected = hmac.new(SECRET_KEY.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(s)):
        raise ValueError("Invalid signature")
    try:
        header = json.loads(_b64url_decode(h))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid token format") from None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise ValueError("Invalid token algorithm")
    try:
        payload = json.loads(_b64url_decode(p))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid token format") from None
    if not isinstance(payload, dict):
        raise ValueError("Invalid token format")
    # `exp` is required. It used to be honoured only when present, so a signed
    # payload without one never expired.
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise ValueError("Token has no expiry")
    if exp < time.time():
        raise ValueError("Token expired")
    return payload

# Argon2id parameters and the hasher live in app/passwords.py, which imports
# nothing that needs the JWT secret: scripts/bootstrap_users.py hashes the
# initial passwords with it and is given no signing key.
from app.passwords import (  # noqa: E402  (re-exported; tests read them here)
    ARGON2_CEILING, ARGON2_DEFAULTS, ARGON2_FLOOR, ARGON2_PREFIX,
    _argon2_params, _hash_password, _password_hasher,
)

# Login is the only unauthenticated endpoint that does deliberately expensive
# work: an Argon2id verification costs ~130 ms and allocates 64 MiB by design.
# Nothing else rate-limits it -- SlowAPIMiddleware is a pass-through stub and
# rate_limiter.check() is called from exactly one analytics endpoint -- so
# without this a handful of concurrent clients can saturate the process by
# guessing passwords. The budget is spent per source address and per account
# separately, so one address cannot exhaust an account's allowance and a
# distributed attempt on a single account is still bounded.
#
# The counter lives in the process, so with multiple workers the effective
# limit is per worker. That is a real limitation and the reason this is a
# bound rather than a lockout.
_login_limiter = RateLimiter(max_requests=10, window_seconds=60)



def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    if hashed.startswith(ARGON2_PREFIX):
        try:
            return _password_hasher.verify(hashed, plain)
        except VerifyMismatchError:
            return False
        except (VerificationError, InvalidHash) as exc:
            # Not a wrong password: the stored value is damaged or the backend
            # failed. The caller still sees plain bad credentials, but silently
            # folding this into that path would hide a corrupted record for as
            # long as nobody tried to log in twice. Neither the hash nor the
            # password is logged.
            logging.getLogger("sora_earth").error(
                "password hash could not be evaluated: %s", type(exc).__name__
            )
            return False
    return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash is not Argon2id at the current parameters."""
    if not hashed or not hashed.startswith(ARGON2_PREFIX):
        return True
    try:
        return _password_hasher.check_needs_rehash(hashed)
    except InvalidHash:
        return True



# Argon2 is expensive on purpose, and the rate limiter bounds attempts per key
# per window -- not how many are in flight at once. A burst spread across
# distinct addresses and accounts stays inside every budget while starting
# arbitrarily many verifications together, and N of them hold roughly
# N x memory_cost. Four concurrent verifications at the default 64 MiB is
# ~256 MiB; without a gate the same burst at the 256 MiB ceiling is unbounded.
#
# The gate is a semaphore rather than a queue: waiting would convert memory
# pressure into latency for everyone, so a saturated process sheds load instead
# and says so.
def _verify_concurrency() -> int:
    raw = os.getenv("SORA_PASSWORD_VERIFY_CONCURRENCY", "4")
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(
            f"SORA_PASSWORD_VERIFY_CONCURRENCY must be an integer, got {raw!r}"
        ) from None
    if not 1 <= value <= 16:
        raise RuntimeError(
            f"SORA_PASSWORD_VERIFY_CONCURRENCY={value} is outside 1..16."
        )
    return value


VERIFY_CONCURRENCY = _verify_concurrency()
_verify_slots = threading.BoundedSemaphore(VERIFY_CONCURRENCY)

# Compared against when the account does not exist, so that an unknown user
# costs the same as a known one. Without it the miss returns before any hashing
# happens, and the difference is a timing oracle for which accounts exist.
# Built once, at import. Doing it per request would make an unknown account
# cost a hash *and* a verify -- turning the defence against enumeration into a
# cheaper way to burn CPU than a real login.
_ABSENT_ACCOUNT_HASH = _password_hasher.hash(os.urandom(32).hex())


def _verify_under_gate(plain: str, stored: str) -> tuple[bool, Optional[str]]:
    """Verify, and compute a re-hash if one is due, under a single slot.

    Returns (matched, new_hash). One acquisition covers both Argon2 operations
    of a login, so a request that already passed admission is never made to
    compete again for its second one. The new hash is only computed here; the
    caller persists it conditionally (app/accounts.py:store_rehash), so the
    slot is never held across a database round trip.
    """
    if not _verify_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="Server busy, retry shortly",
            headers={"Retry-After": "1"},
        )
    try:
        matched = verify_password(plain, stored)
        new_hash = _hash_password(plain) if matched and needs_rehash(stored) else None
        return matched, new_hash
    finally:
        _verify_slots.release()


_ACCOUNT_KEY_SECRET = os.urandom(32)


def _account_key(username: str) -> str:
    """A fixed-width, opaque bucket key for an attacker-supplied username.

    Two reasons it is not the username itself. The key space has to be bounded
    -- an arbitrary-length string straight into a dict is a memory vector -- and
    raw usernames should not sit in a limiter table that ends up in a heap dump
    or a diagnostic endpoint.

    Case and surrounding whitespace are folded so that "Admin " and "admin"
    share one budget; otherwise trivial variations would each buy a fresh
    allowance against the same account.

    Keyed rather than a bare digest: a plain SHA-256 of a username is reversible
    by dictionary, so a heap dump would reveal which accounts were recently
    active. The key is random per process rather than derived from SECRET_KEY --
    the buckets are ephemeral anyway, so there is nothing to correlate across
    restarts, and no production secret is drawn into a new domain.
    """
    normalised = username.strip().casefold()[:256]
    return hmac.new(_ACCOUNT_KEY_SECRET, normalised.encode("utf-8", "replace"),
                    hashlib.sha256).hexdigest()[:32]


def _db_unavailable(exc: Exception) -> HTTPException:
    # No fallback to the environment or to process memory: an account store
    # that cannot be read cannot say who is blocked, so nobody is admitted.
    logger.error("account store unavailable: %s", type(exc).__name__)
    return HTTPException(status_code=503, detail="Account store unavailable",
                         headers={"Retry-After": "5"})


def authenticate(username: str, password: str,
                 client_ip: str | None = None) -> Optional[AccountState]:
    """Look up, verify, and re-check. None for every kind of refusal.

    Unknown, wrong password and blocked all answer None, so the caller gives
    them the same response. Every login route goes through here.

    The account is read twice. Argon2 takes ~130 ms, and a reset or block can
    commit inside that window; issuing tokens from the first read would hand
    out the new generation on the strength of the old password. So the second
    read must show the same version, still unblocked, or the login is refused
    and the caller simply tries again.
    """
    if client_ip is not None:
        # Before the expensive verification, not after.
        _login_limiter.check(f"login-ip:{client_ip}")
        _login_limiter.check(f"login-user:{_account_key(username)}")
    try:
        with SessionLocal() as session:
            before = accounts.state_by_username(session, username)
    except SQLAlchemyError as exc:
        raise _db_unavailable(exc) from None
    # The comparison runs whether or not the account exists, against a hash
    # nobody holds, so a miss costs the same as a hit.
    stored = before.password_hash if before else _ABSENT_ACCOUNT_HASH
    matched, new_hash = _verify_under_gate(password, stored)
    if not matched or before is None or before.is_blocked:
        return None
    try:
        with SessionLocal() as session:
            if new_hash is not None:
                # Best effort. A failure leaves the old hash, which still
                # verifies; the next login tries again.
                try:
                    accounts.store_rehash(session, before, new_hash)
                except SQLAlchemyError:
                    session.rollback()
                    logger.warning("password re-hash could not be stored; retried at next login")
            after = accounts.state_by_id(session, before.id)
    except SQLAlchemyError as exc:
        raise _db_unavailable(exc) from None
    if after is None or after.is_blocked or after.token_version != before.token_version:
        return None
    return after


# Refresh tokens are not kept in a list of the valid ones.
#
# They used to be: a process-local `set` of every token ever issued, and
# validation was membership in it. That set answered one useful question -- has
# this been revoked -- and three unintended ones. A restart emptied it, so every
# refresh token in the world stopped working; `backend` and `scheduler` each had
# their own, so a token issued by one was unknown to the other; and it grew
# without bound, because entries left only by explicit revocation.
#
# Nothing was lost by inverting it. _jwt_decode already verifies the HMAC with a
# constant-time comparison and rejects an expired `exp`, so authenticity and
# lifetime are settled cryptographically before the store is consulted at all.
# The only thing a store can add is revocation, and a list of revoked tokens says
# that directly.
#
# What this changes, stated plainly: a refresh token now survives a restart. That
# was never a security control -- it was mass invalidation as a side effect of
# forgetting, indistinguishable from an outage to the user. Logging someone out
# is revocation, and revocation still works.
_REVOKED_PREFIX = "auth:refresh:revoked:"

# The fallback when Redis is unreachable. Revocation then holds for the process
# that served it and no longer, which is worse than Redis and far better than
# nothing -- and it is exactly the guarantee the old code gave at its best.
_revoked_locally: set = set()


def _token_fingerprint(token: str) -> str:
    """What goes in the store. Never the token itself: a revocation list holding
    live credentials is a credential store nobody decided to build."""
    return hashlib.sha256(token.encode()).hexdigest()


def _redis():
    try:
        from app.redis_cache import redis_client, REDIS_AVAILABLE
    except Exception:
        return None
    return redis_client if REDIS_AVAILABLE else None


def _is_revoked(fingerprint: str) -> bool:
    if fingerprint in _revoked_locally:
        return True
    client = _redis()
    if client is None:
        return False
    try:
        return bool(client.exists(_REVOKED_PREFIX + fingerprint))
    except Exception:
        # Redis went away mid-request. Refusing every refresh would turn a cache
        # outage into a site-wide logout; the local set still holds whatever this
        # process revoked.
        logger.warning("refresh revocation list unreachable; "
                       "revocation is process-local until Redis returns")
        return False

def _session_claims(account) -> dict:
    """`sub` is the account's UUID and `ver` its session generation.

    The role is carried for clients to read, never trusted on the way back in:
    require_auth takes the role from the row.
    """
    return {"sub": str(account.id), "ver": int(account.token_version), "role": account.role}


def create_access_token(account) -> str:
    now = int(time.time())
    payload = dict(_session_claims(account), type="access", iat=now,
                   exp=now + ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    return _jwt_encode(payload)

def create_refresh_token(account) -> str:
    # jti makes the token unique. Without it the payload is (sub, role, type,
    # iat, exp) at one-second resolution, so two logins by the same account in
    # the same second produced byte-identical tokens -- and revoking either one
    # revoked both, because they were the same string. Logging out of one device
    # logged out the other, and no test noticed because both halves looked right.
    #
    # Access tokens are left alone: nothing revokes them individually, so a nonce
    # there would carry no meaning.
    now = int(time.time())
    payload = dict(_session_claims(account), type="refresh", jti=secrets.token_urlsafe(16),
                   iat=now, exp=now + REFRESH_TOKEN_EXPIRE_DAYS * 86400)
    return _jwt_encode(payload)

def validate_refresh_token(token: str) -> dict:
    # Decode first. It is cheap, it is local, and it settles both authenticity
    # and expiry -- so a forged or stale token is refused without a round trip to
    # the revocation list.
    payload = _jwt_decode(token)
    if payload.get("type") != "refresh":
        raise ValueError("Not a refresh token")
    if _is_revoked(_token_fingerprint(token)):
        raise ValueError("Refresh token has been revoked")
    return payload

def revoke_refresh_token(token: str):
    fingerprint = _token_fingerprint(token)
    _revoked_locally.add(fingerprint)
    try:
        payload = _jwt_decode(token)
    except ValueError:
        # Forged, or already past its own expiry. Either way there is nothing
        # left to revoke and no reason to remember it.
        return
    ttl = int(payload.get("exp", 0) - time.time())
    if ttl <= 0:
        return
    client = _redis()
    if client is None:
        return
    try:
        # The entry outlives the token by nothing: once the token expires on its
        # own, _jwt_decode refuses it and the revocation has nothing left to do.
        client.set(_REVOKED_PREFIX + fingerprint, "1", ex=ttl)
    except Exception:
        logger.warning("could not record a refresh-token revocation in Redis; "
                       "it holds for this process only")

def _claims(payload: dict, expected_type: str) -> tuple[uuid.UUID, int]:
    """The claims a session token must carry, or ValueError.

    A token without `ver`, or with the old `sub=<username>`, is refused rather
    than interpreted: the first deployment of persistent accounts asks everyone
    to log in again, which is the price of not trusting a token that cannot be
    revoked.
    """
    if payload.get("type") != expected_type:
        raise ValueError("Invalid token type")
    iat = payload.get("iat")
    if isinstance(iat, bool) or not isinstance(iat, int):
        raise ValueError("Token has no issue time")
    ver = payload.get("ver")
    if isinstance(ver, bool) or not isinstance(ver, int) or ver < 1:
        raise ValueError("Token has no session version")
    try:
        user_id = uuid.UUID(str(payload.get("sub", "")))
    except ValueError:
        raise ValueError("Invalid token subject") from None
    return user_id, ver


def account_for_token(payload: dict, expected_type: str) -> AccountState:
    """The current account a decoded token speaks for, or ValueError.

    Read from the database on every call, uncached: blocking or resetting an
    account takes effect for the next request in every worker, which is the
    whole of the revocation guarantee.
    """
    user_id, ver = _claims(payload, expected_type)
    try:
        with SessionLocal() as session:
            account = accounts.state_by_id(session, user_id)
    except SQLAlchemyError as exc:
        raise _db_unavailable(exc) from None
    if account is None:
        raise ValueError("User not found")
    if account.is_blocked or account.token_version != ver:
        raise ValueError("Session is no longer valid")
    return account


def require_auth(authorization: str = Header(None)) -> UserInfo:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    token = authorization.split(" ", 1)[1]
    try:
        account = account_for_token(_jwt_decode(token), "access")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e), headers={"WWW-Authenticate": "Bearer"})
    return UserInfo(user_id=str(account.id), username=account.username, role=account.role)

def optional_auth(authorization: str = Header(None)) -> Optional[UserInfo]:
    """The caller's account, or None when the request carries no Authorization.

    For routes that serve anonymous callers and behave differently for a
    signed-in one -- /evaluate saves only for an owner, and a signed-in call
    stays out of the shared telemetry.

    Absent is the only case that becomes None. A header that is present goes
    through require_auth unchanged, so a malformed, forged, expired, revoked or
    blocked token is a 401 -- never an anonymous request. Treating a bad token
    as no token would let a revoked session keep using every route that has an
    anonymous path, and would turn "save" into "computed but silently not
    saved". An account store that cannot be read is a 503 for the same reason:
    it cannot say who is blocked.
    """
    if authorization is None:
        return None
    return require_auth(authorization)


def require_admin(authorization: str = Header(None)) -> UserInfo:
    user = require_auth(authorization)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def require_analyst_or_admin(authorization: str = Header(None)) -> UserInfo:
    user = require_auth(authorization)
    if user.role not in ("admin", "analyst"):
        raise HTTPException(status_code=403, detail="Analyst or admin access required")
    return user

def _build_api_keys() -> dict:
    """API keys, from the environment only.

    The literal `demo-key-2026` and `admin-key-2026` used to be here
    unconditionally, so `admin-key-2026` was a published admin credential:
    anyone reading the public source could send it as `X-API-Key` and satisfy
    `require_admin_apikey`. They are gone. Keys now come only from
    `ADMIN_API_KEY` / `API_KEY`, and outside production two named dev keys are
    provided for local use — never in production, where their absence is what
    makes the store fail closed.
    """
    keys: dict = {}
    env_admin = os.getenv("ADMIN_API_KEY")
    if env_admin:
        keys[env_admin] = {"name": "Admin (env)", "role": "admin", "rate_limit": 1000}
    env_user = os.getenv("API_KEY")
    if env_user:
        keys[env_user] = {"name": "User (env)", "role": "user", "rate_limit": 100}
    if SORA_ENV != "production":
        keys.setdefault("dev-admin-key", {"name": "Admin (dev)", "role": "admin", "rate_limit": 1000})
        keys.setdefault("dev-user-key", {"name": "User (dev)", "role": "user", "rate_limit": 100})
    return keys

API_KEYS: dict = _build_api_keys()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def get_api_key(api_key: str = Security(api_key_header)):
    if api_key is None:
        return None
    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return API_KEYS[api_key]

def require_api_key(api_key: str = Security(api_key_header)):
    if api_key is None or api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Valid API key required")
    return API_KEYS[api_key]

def require_admin_apikey(api_key: str = Security(api_key_header)):
    if api_key is None or api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Valid API key required")
    if API_KEYS[api_key]["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return API_KEYS[api_key]
