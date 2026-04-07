"""
SORA.Earth JWT Authentication + RBAC + API Keys
Pure Python JWT (HS256) — no external deps
"""
import hashlib, hmac, json, os, time, base64
from datetime import datetime, timezone
from typing import Optional
from fastapi import Depends, HTTPException, Security, Header, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from pydantic import BaseModel, Field

SECRET_KEY = os.getenv("SORA_JWT_SECRET", "sora-earth-dev-secret-change-in-production-2026")
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

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

def _hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}${h}"

def verify_password(plain: str, hashed: str) -> bool:
    if "$" not in hashed:
        return hashlib.sha256(plain.encode()).hexdigest() == hashed
    salt, h = hashed.split("$", 1)
    return hmac.compare_digest(hashlib.sha256(f"{salt}{plain}".encode()).hexdigest(), h)

USERS_DB: dict = {
    "admin": {"username": "admin", "hashed_password": _hash_password("sora2026"), "role": "admin", "created_at": datetime.now(timezone.utc).isoformat()},
    "analyst": {"username": "analyst", "hashed_password": _hash_password("analyst123"), "role": "analyst", "created_at": datetime.now(timezone.utc).isoformat()},
    "viewer": {"username": "viewer", "hashed_password": _hash_password("viewer123"), "role": "viewer", "created_at": datetime.now(timezone.utc).isoformat()},
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

API_KEYS = {
    "demo-key-2026": {"name": "Demo User", "role": "user", "rate_limit": 100},
    "admin-key-2026": {"name": "Admin", "role": "admin", "rate_limit": 1000},
}
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
