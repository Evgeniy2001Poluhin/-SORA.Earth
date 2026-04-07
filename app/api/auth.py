from fastapi import APIRouter, Depends, HTTPException, Request
from app.auth import (
    Token, LoginRequest, UserInfo,
    verify_password, create_access_token, create_refresh_token,
    require_auth, require_admin, USERS_DB,
    require_api_key, require_admin_apikey,
)
from app.audit import record_audit
from app.metrics import metrics

router = APIRouter()

@router.post("/auth/login", response_model=Token, tags=["auth"])
def login(req: LoginRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    user = USERS_DB.get(req.username)
    if not user or not verify_password(req.password, user["hashed_password"]):
        record_audit(req.username, "login_failed", "/auth/login", "POST", ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token_data = {"sub": user["username"], "role": user["role"]}
    access = create_access_token(token_data)
    refresh = create_refresh_token(token_data)
    record_audit(user["username"], "login", "/auth/login", "POST", ip)
    return Token(access_token=access, refresh_token=refresh)

@router.get("/auth/me", tags=["auth"])
def get_me(user: UserInfo = Depends(require_auth)):
    return {"username": user.username, "role": user.role}

@router.get("/admin/users", tags=["admin"])
def list_users(user: UserInfo = Depends(require_admin)):
    return [{"username": u, "role": d["role"]} for u, d in USERS_DB.items()]

@router.get("/auth/verify", tags=["auth"])
def verify_key(user=Depends(require_api_key)):
    return {"authenticated": True, "user": user["name"], "role": user["role"]}

@router.get("/admin/stats", tags=["admin"])
def admin_stats(user=Depends(require_admin_apikey)):
    return {"metrics": metrics.summary(), "authenticated_as": user["name"]}
