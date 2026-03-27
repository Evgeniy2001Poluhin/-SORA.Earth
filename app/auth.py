import hashlib
import secrets
import time
from pydantic import BaseModel
from typing import Optional
from fastapi import Security, HTTPException, Depends, Header
from fastapi.security import APIKeyHeader


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600

class LoginRequest(BaseModel):
    username: str
    password: str

class UserInfo(BaseModel):
    username: str
    role: str

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

USERS_DB = {
    "admin": {"username": "admin", "hashed_password": _hash("sora2026"), "role": "admin"},
    "analyst": {"username": "analyst", "hashed_password": _hash("analyst123"), "role": "analyst"},
    "viewer": {"username": "viewer", "hashed_password": _hash("viewer123"), "role": "viewer"},
}

_tokens = {}

def verify_password(plain: str, hashed: str) -> bool:
    return _hash(plain) == hashed

def create_access_token(data: dict) -> str:
    token = secrets.token_hex(32)
    _tokens[token] = {"username": data.get("sub", ""), "role": data.get("role", ""), "created": time.time()}
    return token

def get_current_user(token: str) -> Optional[dict]:
    return _tokens.get(token)

def require_auth(authorization: str = Header(None)) -> UserInfo:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    user = _tokens.get(token)
    if not user:
        raise HTTPException(status_code=403, detail="Invalid token")
    return UserInfo(username=user["username"], role=user["role"])

def require_admin(authorization: str = Header(None)) -> UserInfo:
    user = require_auth(authorization)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ============ API KEY AUTH ============
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
