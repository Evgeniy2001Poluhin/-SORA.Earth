"""
SORA.Earth JWT Authentication + RBAC + API Keys
Pure Python JWT (HS256) — no external deps
"""
import hashlib, hmac, json, logging, os, secrets, time, base64
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher

from app.rate_limit import RateLimiter
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
import threading

from fastapi import Depends, HTTPException, Security, Header, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from pydantic import BaseModel, Field

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

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    role: str = Field(default="viewer", pattern="^(admin|analyst|viewer)$")

class UserInfo(BaseModel):
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
    payload = json.loads(_b64url_decode(p))
    if payload.get("exp") and payload["exp"] < time.time():
        raise ValueError("Token expired")
    return payload

# --- password hashing -------------------------------------------------------
#
# Argon2id at the argon2-cffi defaults: time_cost=3, memory_cost=64 MiB,
# parallelism=4, which costs roughly 150 ms to hash and 130 ms to verify on
# commodity hardware. The cost is deliberately not configurable: the only
# reason to expose it would be to lower it, and a weak setting is
# indistinguishable from a strong one until a table leaks.
#
# Two legacy formats are still accepted so that no existing account is locked
# out, and both are upgraded in place on the next successful login:
#
#   $argon2id$...          current
#   <hex salt>$<sha256>    legacy, salted but a single fast round
#   <sha256>               legacy, unsalted -- rainbow-table material
#
# A single SHA-256 round is the wrong primitive for passwords whether or not it
# is salted: commodity GPUs evaluate it in the billions per second.
# OWASP's floor for Argon2id. Raising the parameters as hardware improves is
# expected and is why they are read from the environment at all; lowering them
# past this point is refused outright, because a weak setting is
# indistinguishable from a strong one until a table leaks.
ARGON2_FLOOR = {"time_cost": 2, "memory_cost": 19 * 1024, "parallelism": 1}
ARGON2_DEFAULTS = {"time_cost": 3, "memory_cost": 64 * 1024, "parallelism": 4}
# A ceiling as well as a floor: memory_cost is allocated per concurrent
# verification, so a mistyped value denies service just as effectively as a
# weak one -- in the opposite direction and just as quietly.
# memory_cost is the total for one hash operation. parallelism splits that
# total across lanes -- it does not multiply it. Measured on this build, m=256
# MiB costs ~256 MiB of peak RSS at p=1, 2, 4 and 8 alike.
#
# So the multiplier is concurrency, not parallelism: N verifications in flight
# hold roughly N x memory_cost at once. Nothing in the app caps N -- the rate
# limiter bounds requests per key per minute, not simultaneous ones -- and no
# container in docker-compose.prod.yml declares a memory limit, so a mistyped
# cost is bounded only by host RAM.
#
# 256 MiB is therefore a configuration guard rather than a proof of safety: it
# keeps a typo from reserving gigabytes per verification, while eight
# concurrent logins at the ceiling would still hold ~2 GiB. Bounding N belongs
# with a declared container limit and an admission bound, and should come
# before this ceiling is raised.
ARGON2_CEILING = {"time_cost": 10, "memory_cost": 256 * 1024, "parallelism": 8}


def _argon2_params() -> dict:
    params = {}
    for name, default in ARGON2_DEFAULTS.items():
        raw = os.getenv(f"SORA_ARGON2_{name.upper()}")
        if raw is None:
            params[name] = default
            continue
        try:
            value = int(raw)
        except ValueError:
            raise RuntimeError(
                f"SORA_ARGON2_{name.upper()} must be an integer, got {raw!r}"
            ) from None
        if value < ARGON2_FLOOR[name]:
            raise RuntimeError(
                f"SORA_ARGON2_{name.upper()}={value} is below the floor "
                f"{ARGON2_FLOOR[name]}. Refusing to weaken password hashing."
            )
        if value > ARGON2_CEILING[name]:
            raise RuntimeError(
                f"SORA_ARGON2_{name.upper()}={value} exceeds the ceiling "
                f"{ARGON2_CEILING[name]}. A cost this high denies service."
            )
        params[name] = value
    return params


_password_hasher = PasswordHasher(**_argon2_params())

ARGON2_PREFIX = "$argon2"

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


def _hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def _verify_legacy_sha256(plain: str, hashed: str) -> bool:
    """Constant-time check against either legacy format."""
    if "$" in hashed:
        salt, stored = hashed.split("$", 1)
        candidate = hashlib.sha256(f"{salt}{plain}".encode()).hexdigest()
    else:
        salt, stored = "", hashed
        candidate = hashlib.sha256(plain.encode()).hexdigest()
    return hmac.compare_digest(candidate, stored)


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
    return _verify_legacy_sha256(plain, hashed)


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash is not Argon2id at the current parameters."""
    if not hashed or not hashed.startswith(ARGON2_PREFIX):
        return True
    try:
        return _password_hasher.check_needs_rehash(hashed)
    except InvalidHash:
        return True


def upgrade_password_hash(user: dict, plain: str) -> bool:
    """Re-hash in place after a successful login. Returns True if it changed.

    Called only once the password has already been verified, so the plaintext
    is known-good. This is what keeps the legacy formats from becoming
    permanent: every active account migrates the next time its owner signs in.
    """
    if not needs_rehash(user.get("hashed_password", "")):
        return False
    user["hashed_password"] = _hash_password(plain)
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


def _verify_and_upgrade_under_gate(plain: str, stored: str,
                                  user: dict | None) -> bool:
    """Verify, and re-hash if needed, under a single slot.

    One acquisition covers both. Releasing between them and taking a second
    slot would put the same login through the gate twice: the bound would still
    hold, but a request that already passed admission could be made to compete
    again, and the two Argon2 operations of one login would no longer be
    accounted together.

    A stale hash is therefore always replaced on a successful verification:
    holding the slot already, there is no second admission to fail and no
    reason to skip. Nothing is deferred and nothing runs after the response.

    Should a future store make persisting the new hash fallible -- these records
    live in a process dictionary today -- the login must still succeed, and the
    replacement is simply attempted again at the next successful login. That is
    the same mechanism that migrates a legacy account in the first place.
    """
    if not _verify_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="Server busy, retry shortly",
            headers={"Retry-After": "1"},
        )
    try:
        matched = verify_password(plain, stored)
        if matched and user is not None and needs_rehash(stored):
            upgrade_password_hash(user, plain)
        return matched
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


def authenticate(username: str, password: str, users: dict | None = None,
                 client_ip: str | None = None) -> Optional[dict]:
    """Look up, verify, and migrate the stored hash. None when either fails.

    Every login path goes through here deliberately. Verifying and upgrading in
    separate places invites a fourth login route that authenticates correctly
    but forgets the upgrade, which would keep the legacy formats alive
    indefinitely and quietly.
    """
    if client_ip is not None:
        # Before the expensive verification, not after.
        _login_limiter.check(f"login-ip:{client_ip}")
        _login_limiter.check(f"login-user:{_account_key(username)}")
    store = USERS_DB if users is None else users
    user = store.get(username)
    # The comparison runs whether or not the account exists, against a hash
    # nobody holds, so a miss costs the same as a hit.
    stored = user.get("hashed_password", "") if user else _ABSENT_ACCOUNT_HASH
    if not _verify_and_upgrade_under_gate(password, stored, user):
        return None
    return user


def legacy_hash_count(users: dict | None = None) -> int:
    """How many stored hashes are still not Argon2id.

    The unsalted branch above can only be deleted once this reaches zero and
    stays there, so the removal date is evidence rather than a guess.
    """
    store = USERS_DB if users is None else users
    return sum(
        1 for u in store.values()
        if not str(u.get("hashed_password", "")).startswith(ARGON2_PREFIX)
    )

# Load default users from environment (fallback to dev passwords only in dev mode)
def _get_default_password(role: str, dev_default: str) -> str:
    """Get password from env or use dev default only if not in production."""
    env_key = f"SORA_DEFAULT_{role.upper()}_PASSWORD"
    password = os.getenv(env_key)
    if password:
        return password
    if SORA_ENV == "production":
        raise RuntimeError(
            f"CRITICAL: Production deployment requires {env_key} environment variable. "
            f"Default passwords are disabled in production for security."
        )
    return dev_default

USERS_DB: dict = {
    "admin": {
        "username": "admin",
        "hashed_password": _hash_password(_get_default_password("admin", "sora2026")),
        "role": "admin",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    "analyst": {
        "username": "analyst",
        "hashed_password": _hash_password(_get_default_password("analyst", "analyst123")),
        "role": "analyst",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    "viewer": {
        "username": "viewer",
        "hashed_password": _hash_password(_get_default_password("viewer", "viewer123")),
        "role": "viewer",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
}

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

def create_access_token(data: dict) -> str:
    payload = {"sub": data.get("sub", ""), "role": data.get("role", ""), "type": "access", "iat": int(time.time()), "exp": int(time.time()) + ACCESS_TOKEN_EXPIRE_MINUTES * 60}
    return _jwt_encode(payload)

def create_refresh_token(data: dict) -> str:
    # jti makes the token unique. Without it the payload is (sub, role, type,
    # iat, exp) at one-second resolution, so two logins by the same account in
    # the same second produced byte-identical tokens -- and revoking either one
    # revoked both, because they were the same string. Logging out of one device
    # logged out the other, and no test noticed because both halves looked right.
    #
    # Access tokens are left alone: nothing revokes them individually, so a nonce
    # there would carry no meaning.
    payload = {"sub": data.get("sub", ""), "role": data.get("role", ""), "type": "refresh",
               "jti": secrets.token_urlsafe(16),
               "iat": int(time.time()), "exp": int(time.time()) + REFRESH_TOKEN_EXPIRE_DAYS * 86400}
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

def require_auth(authorization: str = Header(None)) -> UserInfo:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    token = authorization.split(" ", 1)[1]
    try:
        payload = _jwt_decode(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e), headers={"WWW-Authenticate": "Bearer"})
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = USERS_DB.get(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return UserInfo(username=user["username"], role=user["role"])

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
    keys = {
        "demo-key-2026": {"name": "Demo User", "role": "user", "rate_limit": 100},
        "admin-key-2026": {"name": "Admin", "role": "admin", "rate_limit": 1000},
    }
    env_admin = os.getenv("ADMIN_API_KEY")
    if env_admin:
        keys[env_admin] = {"name": "Admin (env)", "role": "admin", "rate_limit": 1000}
    env_user = os.getenv("API_KEY")
    if env_user:
        keys[env_user] = {"name": "User (env)", "role": "user", "rate_limit": 100}
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
