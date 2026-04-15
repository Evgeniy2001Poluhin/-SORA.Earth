"""Auth + Audit routes for SORA.Earth"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from datetime import datetime, timezone
from app.auth import (
    Token, UserCreate, UserInfo, USERS_DB,
    _hash_password, verify_password,
    create_access_token, create_refresh_token,
    validate_refresh_token, revoke_refresh_token,
    require_auth, require_admin, require_api_key, require_admin_apikey,
)
from app.audit import record_audit, get_audit_log
from app.metrics import metrics

router = APIRouter()

class RefreshRequest(BaseModel):
    refresh_token: str

@router.post("/auth/login", response_model=Token, tags=["auth"])
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    user = USERS_DB.get(form_data.username)
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        record_audit(form_data.username, "login_failed", "/auth/login", "POST",
                     request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token_data = {"sub": user["username"], "role": user["role"]}
    access = create_access_token(token_data)
    refresh = create_refresh_token(token_data)
    record_audit(user["username"], "login", "/auth/login", "POST",
                 request.client.host if request.client else "unknown")
    return Token(access_token=access, refresh_token=refresh)


class JsonLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login-json", response_model=Token, tags=["auth"])
def login_json(request: Request, body: JsonLoginRequest):
    user = USERS_DB.get(body.username)
    if not user or not verify_password(body.password, user["hashed_password"]):
        record_audit(body.username, "login_failed", "/auth/login-json", "POST",
                     request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token_data = {"sub": user["username"], "role": user["role"]}
    access = create_access_token(token_data)
    refresh = create_refresh_token(token_data)
    record_audit(user["username"], "login", "/auth/login-json", "POST",
                 request.client.host if request.client else "unknown")
    return Token(access_token=access, refresh_token=refresh)

@router.post("/auth/refresh", response_model=Token, tags=["auth"])
def refresh_token(request: Request, body: RefreshRequest):
    try:
        payload = validate_refresh_token(body.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    revoke_refresh_token(body.refresh_token)
    token_data = {"sub": payload["sub"], "role": payload["role"]}
    access = create_access_token(token_data)
    refresh = create_refresh_token(token_data)
    record_audit(payload["sub"], "token_refresh", "/auth/refresh", "POST",
                 request.client.host if request.client else "unknown")
    return Token(access_token=access, refresh_token=refresh)

@router.post("/auth/register", tags=["auth"])
def register_user(request: Request, user_data: UserCreate,
                  current_user: UserInfo = Depends(require_admin)):
    if user_data.username in USERS_DB:
        raise HTTPException(status_code=409, detail="Username already exists")
    USERS_DB[user_data.username] = {
        "username": user_data.username, "hashed_password": _hash_password(user_data.password),
        "role": user_data.role, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    record_audit(current_user.username, "register_user", "/auth/register", "POST",
                 request.client.host if request.client else "unknown",
                 details=f"created '{user_data.username}' role='{user_data.role}'")
    return {"username": user_data.username, "role": user_data.role}

@router.get("/auth/me", tags=["auth"])
def get_me(user: UserInfo = Depends(require_auth)):
    return {"username": user.username, "role": user.role}

@router.get("/admin/users", tags=["admin"])
def list_users(user: UserInfo = Depends(require_admin)):
    return [{"username": u, "role": d["role"]} for u, d in USERS_DB.items()]

@router.get("/admin/stats", tags=["admin"])
def admin_stats(user=Depends(require_admin_apikey)):
    return {"metrics": metrics.summary(), "authenticated_as": user["name"]}

@router.get("/auth/verify", tags=["auth"])
def verify_key(user=Depends(require_api_key)):
    return {"authenticated": True, "user": user["name"], "role": user["role"]}

@router.get("/audit/log", tags=["admin"])
def get_audit(limit: int = 100, user: str = None,
              current_user: UserInfo = Depends(require_admin)):
    return get_audit_log(limit=limit, user=user)
