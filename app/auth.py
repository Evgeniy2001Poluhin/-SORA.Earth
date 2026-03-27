import os, hashlib, hmac
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

SECRET_KEY = os.getenv("JWT_SECRET", "sora-earth-secret-key-2026-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

security = HTTPBearer(auto_error=False)

def _hash_pw(password: str) -> str:
    salt = "sora-earth-static-salt-2026"
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()

def _verify_pw(password: str, hashed: str) -> bool:
    return hmac.compare_digest(_hash_pw(password), hashed)

USERS_DB = {
    "admin": {"username": "admin", "hashed_password": _hash_pw("sora2026"), "role": "admin"},
    "analyst": {"username": "analyst", "hashed_password": _hash_pw("analyst123"), "role": "analyst"},
    "viewer": {"username": "viewer", "hashed_password": _hash_pw("viewer123"), "role": "viewer"},
}

class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

class LoginRequest(BaseModel):
    username: str
    password: str

class UserInfo(BaseModel):
    username: str
    role: str

def verify_password(plain, hashed):
    return _verify_pw(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[UserInfo]:
    if credentials is None:
        return None
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role", "viewer")
        if username is None:
            return None
        return UserInfo(username=username, role=role)
    except JWTError:
        return None

def require_auth(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UserInfo:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role", "viewer")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return UserInfo(username=username, role=role)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def require_admin(user: UserInfo = Depends(require_auth)) -> UserInfo:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
