"""
SORA.Earth JWT Authentication + RBAC + API Keys
Pure Python JWT (HS256) — no external deps
"""
import hashlib, hmac, json, os, time, base64
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher

from app.rate_limit import RateLimiter
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Security, Header, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from pydantic import BaseModel, Field

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
        except (VerifyMismatchError, VerificationError, InvalidHash):
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
        _login_limiter.check(f"login-user:{username}")
    store = USERS_DB if users is None else users
    user = store.get(username)
    if not user or not verify_password(password, user.get("hashed_password", "")):
        return None
    upgrade_password_hash(user, password)
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

_refresh_tokens: set = set()

def create_access_token(data: dict) -> str:
    payload = {"sub": data.get("sub", ""), "role": data.get("role", ""), "type": "access", "iat": int(time.time()), "exp": int(time.time()) + ACCESS_TOKEN_EXPIRE_MINUTES * 60}
    return _jwt_encode(payload)

def create_refresh_token(data: dict) -> str:
    payload = {"sub": data.get("sub", ""), "role": data.get("role", ""), "type": "refresh", "iat": int(time.time()), "exp": int(time.time()) + REFRESH_TOKEN_EXPIRE_DAYS * 86400}
    token = _jwt_encode(payload)
    _refresh_tokens.add(token)
    return token

def validate_refresh_token(token: str) -> dict:
    if token not in _refresh_tokens:
        raise ValueError("Refresh token not found or already used")
    payload = _jwt_decode(token)
    if payload.get("type") != "refresh":
        raise ValueError("Not a refresh token")
    return payload

def revoke_refresh_token(token: str):
    _refresh_tokens.discard(token)

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
