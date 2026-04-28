"""Authentication Routes — Login, logout, registration, and user management endpoints."""

from __future__ import annotations

import os
import uuid
from typing import Optional

from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: F401
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.user import UserRepository
from db.repositories.user_session import UserSessionRepository
from models.user import TokenData
from services import auth
from services.activity_logger import ActivityLogger
from services.database import get_db


def get_client_ip(request: Request) -> str:
    """Get client IP address from request."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


router = APIRouter(prefix="/api/auth", tags=["auth"])
templates = Jinja2Templates(directory="templates")

COOKIE_NAME = "access_token"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"


def set_auth_cookie(response: Response, token: str) -> None:
    """Set the authentication cookie."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def clear_auth_cookie(response: Response) -> None:
    """Clear the authentication cookie."""
    response.delete_cookie(key=COOKIE_NAME)


def get_token_from_request(request: Request) -> Optional[str]:
    """Extract token from cookie or Authorization header."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


# get_current_user_optional and get_current_user are sync in middleware/routes
# but decode_token is now async — these dependency functions must be async too.


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[TokenData]:
    """Get current user if authenticated, None otherwise."""
    token = get_token_from_request(request)
    if not token:
        return None
    payload = await auth.decode_token(db, token)
    if not payload:
        return None
    return TokenData(
        user_id=str(payload["sub"]),
        email=str(payload["email"]),
        session_id=str(payload["jti"]),
        is_admin=bool(payload.get("is_admin", False)),
    )


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenData:
    """Get current user, raise 401 if not authenticated."""
    user = await get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenData:
    """Require admin user, raise 403 if not admin."""
    user = await get_current_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ============== Login / Logout ==============


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Authenticate user and set session cookie."""
    client_ip = get_client_ip(request)
    user, error = await auth.authenticate_user(db, email, password)
    if error:
        ActivityLogger.admin_login_failed(email, error, ip_address=client_ip)
        raise HTTPException(status_code=401, detail=error)
    assert user is not None
    token = await auth.create_access_token(
        db,
        user_id=str(user["id"]),
        email=str(user["email"]),
        is_admin=bool(user.get("is_admin", False)),
    )
    ActivityLogger.admin_login(
        str(user["id"]), str(user["email"]), ip_address=client_ip
    )
    set_auth_cookie(response, token)
    return {
        "status": "success",
        "user": {"email": user["email"], "display_name": user.get("display_name")},
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Logout user and clear session."""
    user = await get_current_user_optional(request, db)
    if user:
        await auth.logout_user(db, user.session_id)
        ActivityLogger.admin_logout(
            user.user_id, user.email, ip_address=get_client_ip(request)
        )
    clear_auth_cookie(response)
    return {"status": "success"}


@router.get("/me")
async def get_me(
    request: Request,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Get current user info."""
    repo = UserRepository(db)
    user = await repo.get(uuid.UUID(current_user.user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


# ============== Registration ==============


@router.post("/register")
async def register(
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    display_name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Register a new user account."""
    if os.getenv("DISABLE_REGISTRATION", "false").lower() == "true":
        raise HTTPException(status_code=403, detail="Registration is disabled")
    user_dict, error = await auth.register_user(db, email, password, display_name)
    if error:
        raise HTTPException(status_code=400, detail=error)
    assert user_dict is not None
    token = await auth.create_access_token(
        db,
        user_id=str(user_dict["id"]),
        email=str(user_dict["email"]),
        is_admin=bool(user_dict.get("is_admin", False)),
    )
    set_auth_cookie(response, token)
    return {"status": "success", "user_id": str(user_dict["id"])}


# ============== Password Management ==============


@router.get("/change-password-modal", response_class=HTMLResponse)
async def change_password_modal(
    request: Request,
    current_user: TokenData = Depends(get_current_user),
) -> HTMLResponse:
    """Return the change password modal HTML."""
    return templates.TemplateResponse(
        "partials/change_password_modal.html",
        {
            "request": request,
        },
    )


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Change current user's password."""
    success, error = await auth.change_password(
        db, current_user.user_id, current_password, new_password
    )
    if not success:
        raise HTTPException(status_code=400, detail=error)
    ActivityLogger.admin_password_changed(
        current_user.user_id,
        current_user.email,
        ip_address=get_client_ip(request),
    )
    return {"status": "success", "message": "Password changed. Please log in again."}


# ============== Admin: User Management ==============


@router.get("/users")
async def list_users(
    admin: TokenData = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List all users (admin only)."""
    repo = UserRepository(db)
    users = await repo.list(limit=1000, order_by=None)
    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "is_admin": u.is_admin,
                "created_at": u.created_at,
                "last_login": u.last_login,
            }
            for u in users
        ]
    }


@router.get("/invite-modal", response_class=HTMLResponse)
async def invite_user_modal(
    request: Request,
    admin: TokenData = Depends(require_admin),
) -> HTMLResponse:
    """Return the invite user modal HTML (admin only)."""
    return templates.TemplateResponse(
        "partials/invite_user_modal.html",
        {
            "request": request,
        },
    )


@router.post("/users/invite", response_class=HTMLResponse)
async def invite_user(
    request: Request,
    email: str = Form(...),
    is_admin: bool = Form(False),
    admin: TokenData = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Invite a new user with temporary password (admin only)."""
    user_id, temp_password, error = await auth.create_invite_user(db, email, is_admin)
    if error:
        return templates.TemplateResponse(
            "partials/invite_user_modal.html",
            {
                "request": request,
                "error": error,
            },
        )
    assert user_id is not None
    ActivityLogger.admin_user_created(
        admin.user_id,
        email,
        user_id,
        is_admin=is_admin,
        ip_address=get_client_ip(request),
    )
    return templates.TemplateResponse(
        "partials/invite_user_modal.html",
        {
            "request": request,
            "temp_password": temp_password,
            "email": email,
        },
    )


@router.patch("/users/{user_id}/role", response_class=HTMLResponse)
async def change_user_role(
    request: Request,
    user_id: str,
    is_admin: bool = Form(...),
    admin: TokenData = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Change a user's role (admin only)."""
    repo = UserRepository(db)
    current_user_row = await repo.get(uuid.UUID(admin.user_id))
    current_user_dict = (
        {
            "id": str(current_user_row.id),
            "email": current_user_row.email,
            "display_name": current_user_row.display_name,
            "is_admin": current_user_row.is_admin,
        }
        if current_user_row
        else {}
    )

    if user_id == admin.user_id:
        users = await repo.list(limit=1000)
        return templates.TemplateResponse(
            "partials/account_tab.html",
            {
                "request": request,
                "current_user": current_user_dict,
                "users": [
                    {
                        "id": str(u.id),
                        "email": u.email,
                        "display_name": u.display_name,
                        "is_admin": u.is_admin,
                    }
                    for u in users
                ],
                "is_admin": True,
                "error": "Cannot change your own role",
            },
        )

    success, error = await auth.change_user_role(db, user_id, is_admin)
    if not success:
        users = await repo.list(limit=1000)
        return templates.TemplateResponse(
            "partials/account_tab.html",
            {
                "request": request,
                "current_user": current_user_dict,
                "users": [
                    {
                        "id": str(u.id),
                        "email": u.email,
                        "display_name": u.display_name,
                        "is_admin": u.is_admin,
                    }
                    for u in users
                ],
                "is_admin": True,
                "error": error,
            },
        )

    users = await repo.list(limit=1000)
    target_user = await repo.get(uuid.UUID(user_id))
    role_action = "promoted to admin" if is_admin else "demoted to user"
    target_email = target_user.email if target_user else "unknown"
    ActivityLogger.admin_role_changed(
        admin.user_id,
        user_id,
        target_email,
        "admin" if is_admin else "user",
        ip_address=get_client_ip(request),
    )
    return templates.TemplateResponse(
        "partials/account_tab.html",
        {
            "request": request,
            "current_user": current_user_dict,
            "users": [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "display_name": u.display_name,
                    "is_admin": u.is_admin,
                }
                for u in users
            ],
            "is_admin": True,
            "success": f"{target_email} {role_action}",
        },
    )


@router.delete("/users/{user_id}", response_class=HTMLResponse)
async def delete_user(
    request: Request,
    user_id: str,
    admin: TokenData = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Delete a user (admin only)."""
    repo = UserRepository(db)
    current_user_row = await repo.get(uuid.UUID(admin.user_id))
    current_user_dict = (
        {
            "id": str(current_user_row.id),
            "email": current_user_row.email,
            "display_name": current_user_row.display_name,
            "is_admin": current_user_row.is_admin,
        }
        if current_user_row
        else {}
    )

    if user_id == admin.user_id:
        users = await repo.list(limit=1000)
        return templates.TemplateResponse(
            "partials/account_tab.html",
            {
                "request": request,
                "current_user": current_user_dict,
                "users": [
                    {
                        "id": str(u.id),
                        "email": u.email,
                        "display_name": u.display_name,
                        "is_admin": u.is_admin,
                    }
                    for u in users
                ],
                "is_admin": True,
                "error": "Cannot delete your own account",
            },
        )

    deleted_user_row = await repo.get(uuid.UUID(user_id))
    deleted_email = deleted_user_row.email if deleted_user_row else "unknown"

    sess_repo = UserSessionRepository(db)
    await sess_repo.revoke_all_user_sessions(uuid.UUID(user_id))

    deleted = await repo.delete(uuid.UUID(user_id))
    if not deleted:
        users = await repo.list(limit=1000)
        return templates.TemplateResponse(
            "partials/account_tab.html",
            {
                "request": request,
                "current_user": current_user_dict,
                "users": [
                    {
                        "id": str(u.id),
                        "email": u.email,
                        "display_name": u.display_name,
                        "is_admin": u.is_admin,
                    }
                    for u in users
                ],
                "is_admin": True,
                "error": "User not found",
            },
        )

    await db.commit()
    ActivityLogger.admin_user_deleted(
        admin.user_id,
        deleted_email,
        user_id,
        ip_address=get_client_ip(request),
    )
    users = await repo.list(limit=1000)
    return templates.TemplateResponse(
        "partials/account_tab.html",
        {
            "request": request,
            "current_user": current_user_dict,
            "users": [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "display_name": u.display_name,
                    "is_admin": u.is_admin,
                }
                for u in users
            ],
            "is_admin": True,
            "success": "User deleted successfully",
        },
    )


# ============== Session Management ==============


@router.post("/logout-all")
async def logout_all_sessions(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Logout from all sessions."""
    count = await auth.logout_all_sessions(db, current_user.user_id)
    return {"status": "success", "sessions_revoked": count}
