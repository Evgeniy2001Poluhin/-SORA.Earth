from fastapi import APIRouter, Depends, HTTPException
from app.auth import (
    Token, LoginRequest, UserInfo,
    verify_password, create_access_token,
    require_auth, require_admin, USERS_DB,
    require_api_key, require_admin_apikey,
)
from app.metrics import metrics


router = APIRouter()


@router.post("/auth/login", response_model=Token, tags=["auth"])
def login(req: LoginRequest):
    user = USERS_DB.get(req.username)
    if not user or not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user["username"], "role": user["role"]})
    return Token(access_token=token, token_type="bearer", expires_in=3600)


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
