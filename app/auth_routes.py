"""Auth + Audit routes for SORA.Earth"""
import uuid
from typing import Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app import accounts
from app.auth import (
    Token, UserInfo,
    authenticate, account_for_token, _db_unavailable, _hash_password,
    create_access_token, create_refresh_token,
    validate_refresh_token, revoke_refresh_token,
    require_auth, require_admin, require_api_key, require_admin_apikey,
)
from app.audit import record_audit, get_audit_log
from app.database import SessionLocal
from app.rate_limit import client_address
from app.metrics import metrics


class _NoInputEchoRoute(APIRoute):
    """A 422 from these routes says where the body was wrong, never what it held.

    FastAPI's default validation error carries the offending `input`, and on a
    login or a password reset that input is a password. Location and message
    are kept; the value is dropped.
    """

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def scrubbed(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise RequestValidationError([
                    {k: v for k, v in err.items() if k not in ("input", "ctx", "url")}
                    for err in exc.errors()
                ]) from None

        return scrubbed


router = APIRouter(route_class=_NoInputEchoRoute)

class RefreshRequest(BaseModel):
    refresh_token: str

@router.post("/auth/login", response_model=Token, tags=["auth"])
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    client_ip = client_address(request)
    user = authenticate(form_data.username, form_data.password, client_ip=client_ip)
    if not user:
        record_audit(form_data.username, "login_failed", "/auth/login", "POST",
                     request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access = create_access_token(user)
    refresh = create_refresh_token(user)
    record_audit(user.username, "login", "/auth/login", "POST",
                 request.client.host if request.client else "unknown")
    return Token(access_token=access, refresh_token=refresh)


class JsonLoginRequest(BaseModel):
    username: str
    password: SecretStr


@router.post("/auth/login-json", response_model=Token, tags=["auth"])
def login_json(request: Request, body: JsonLoginRequest):
    client_ip = client_address(request)
    user = authenticate(body.username, body.password.get_secret_value(), client_ip=client_ip)
    if not user:
        record_audit(body.username, "login_failed", "/auth/login-json", "POST",
                     request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access = create_access_token(user)
    refresh = create_refresh_token(user)
    record_audit(user.username, "login", "/auth/login-json", "POST",
                 request.client.host if request.client else "unknown")
    return Token(access_token=access, refresh_token=refresh)

@router.post("/auth/refresh", response_model=Token, tags=["auth"])
def refresh_token(request: Request, body: RefreshRequest):
    try:
        payload = validate_refresh_token(body.refresh_token)
        # The account, not the token, decides: a blocked or reset account's
        # refresh token is refused here even though its signature is valid.
        account = account_for_token(payload, "refresh")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    revoke_refresh_token(body.refresh_token)
    access = create_access_token(account)
    refresh = create_refresh_token(account)
    record_audit(account.username, "token_refresh", "/auth/refresh", "POST",
                 request.client.host if request.client else "unknown")
    return Token(access_token=access, refresh_token=refresh)

# POST /auth/register was here and is gone, and stays gone. It answered 200 and
# lost the account on the next restart, because accounts lived in a dict. They
# now live in the `users` table, and are created by an administrator -- there
# is still no self-service registration.

@router.get("/auth/me", tags=["auth"])
def get_me(user: UserInfo = Depends(require_auth)):
    return {"user_id": user.user_id, "username": user.username, "role": user.role}


class AccountView(BaseModel):
    """What the API says about an account: never the hash or session version."""
    user_id: str
    username: str
    role: str
    is_blocked: bool
    created_at: Optional[str] = None
    password_changed_at: Optional[str] = None


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: SecretStr
    role: Literal["admin", "analyst", "viewer", "specialist"] = "specialist"


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr


class UpdateAccessRequest(BaseModel):
    # Only these two. username, id, the hash and the session version are not
    # writable through a PATCH, and an unknown field is a 422, not ignored.
    model_config = ConfigDict(extra="forbid")
    is_blocked: Optional[bool] = None
    role: Optional[Literal["admin", "analyst", "viewer", "specialist"]] = None


def _account_call(fn):
    try:
        with SessionLocal() as session:
            return fn(session)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    except SQLAlchemyError as exc:
        raise _db_unavailable(exc) from None


def _audit(request: Request, actor: UserInfo, action: str, path: str, method: str):
    record_audit(actor.username, action, path, method,
                 request.client.host if request.client else "unknown")


@router.get("/admin/users", response_model=list[AccountView], tags=["admin"])
def list_users(user: UserInfo = Depends(require_admin)):
    return _account_call(lambda s: [accounts.public_view(u) for u in accounts.list_users(s)])


@router.post("/admin/users", status_code=201, response_model=AccountView, tags=["admin"])
def create_user(request: Request, body: CreateUserRequest,
                user: UserInfo = Depends(require_admin)):
    created = _account_call(lambda s: accounts.public_view(accounts.create_user(
        s, username=body.username, password=body.password.get_secret_value(), role=body.role,
        created_by=uuid.UUID(user.user_id), hash_password=_hash_password)))
    _audit(request, user, f"user_create:{created['user_id']}", "/admin/users", "POST")
    return created


@router.post("/admin/users/{user_id}/reset-password", response_model=AccountView, tags=["admin"])
def reset_password(request: Request, user_id: uuid.UUID, body: ResetPasswordRequest,
                   user: UserInfo = Depends(require_admin)):
    updated = _account_call(lambda s: accounts.public_view(accounts.reset_password(
        s, user_id, body.password.get_secret_value(), hash_password=_hash_password)))
    _audit(request, user, f"user_reset_password:{user_id}", "/admin/users/{id}/reset-password", "POST")
    return updated


@router.patch("/admin/users/{user_id}", response_model=AccountView, tags=["admin"])
def update_user(request: Request, user_id: uuid.UUID, body: UpdateAccessRequest,
                user: UserInfo = Depends(require_admin)):
    updated = _account_call(lambda s: accounts.public_view(accounts.update_access(
        s, actor_id=uuid.UUID(user.user_id), user_id=user_id,
        is_blocked=body.is_blocked, role=body.role)))
    _audit(request, user, f"user_update_access:{user_id}", "/admin/users/{id}", "PATCH")
    return updated


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
