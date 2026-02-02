"""
Authentication Routes
Login, logout, registration, and user management endpoints
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from services import auth
from services import database as db
from models.user import User, TokenData

router = APIRouter(prefix="/api/auth", tags=["auth"])
templates = Jinja2Templates(directory="templates")


# Cookie configuration
COOKIE_NAME = "access_token"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
COOKIE_SAMESITE = "lax"


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
    # Try cookie first
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token

    # Fall back to Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


def get_current_user_optional(request: Request) -> Optional[TokenData]:
    """Get current user if authenticated, None otherwise."""
    token = get_token_from_request(request)
    if not token:
        return None

    payload = auth.decode_token(token)
    if not payload:
        return None

    return TokenData(
        user_id=payload["sub"],
        email=payload["email"],
        session_id=payload["jti"],
        is_admin=payload.get("is_admin", False),
    )


def get_current_user(request: Request) -> TokenData:
    """Get current user, raise 401 if not authenticated."""
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> TokenData:
    """Require admin user, raise 403 if not admin."""
    user = get_current_user(request)
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
):
    """Authenticate user and set session cookie."""
    user, error = auth.authenticate_user(email, password)
    if error:
        raise HTTPException(status_code=401, detail=error)

    # Create token
    token = auth.create_access_token(
        user_id=user["id"],
        email=user["email"],
        is_admin=user.get("is_admin", False),
    )

    # Set cookie
    set_auth_cookie(response, token)

    return {"status": "success", "user": {"email": user["email"], "display_name": user.get("display_name")}}


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Logout user and clear session."""
    user = get_current_user_optional(request)
    if user:
        auth.logout_user(user.session_id)

    clear_auth_cookie(response)
    return {"status": "success"}


@router.get("/me")
async def get_me(request: Request, current_user: TokenData = Depends(get_current_user)):
    """Get current user info."""
    user = db.get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name"),
        "is_admin": user.get("is_admin", False),
    }


# ============== Registration ==============

@router.post("/register")
async def register(
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    display_name: Optional[str] = Form(None),
):
    """Register a new user account."""
    # Check if registration is enabled
    if os.getenv("DISABLE_REGISTRATION", "false").lower() == "true":
        raise HTTPException(status_code=403, detail="Registration is disabled")

    user_id, error = auth.register_user(email, password, display_name)
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Get user and create token
    user = db.get_user_by_id(user_id)
    token = auth.create_access_token(
        user_id=user["id"],
        email=user["email"],
        is_admin=user.get("is_admin", False),
    )

    set_auth_cookie(response, token)
    return {"status": "success", "user_id": user_id}


# ============== Password Management ==============

@router.get("/change-password-modal", response_class=HTMLResponse)
async def change_password_modal(
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    """Return the change password modal HTML."""
    return templates.TemplateResponse("partials/change_password_modal.html", {
        "request": request,
    })


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: TokenData = Depends(get_current_user),
):
    """Change current user's password."""
    success, error = auth.change_password(
        current_user.user_id, current_password, new_password
    )
    if not success:
        raise HTTPException(status_code=400, detail=error)

    return {"status": "success", "message": "Password changed. Please log in again."}


# ============== Admin: User Management ==============

@router.get("/users")
async def list_users(admin: TokenData = Depends(require_admin)):
    """List all users (admin only)."""
    users = db.list_users()
    return {"users": users}


@router.get("/invite-modal", response_class=HTMLResponse)
async def invite_user_modal(
    request: Request,
    admin: TokenData = Depends(require_admin),
):
    """Return the invite user modal HTML (admin only)."""
    return templates.TemplateResponse("partials/invite_user_modal.html", {
        "request": request,
    })


@router.post("/users/invite", response_class=HTMLResponse)
async def invite_user(
    request: Request,
    email: str = Form(...),
    is_admin: bool = Form(False),
    admin: TokenData = Depends(require_admin),
):
    """Invite a new user with temporary password (admin only)."""
    user_id, temp_password, error = auth.create_invite_user(email, is_admin)
    if error:
        return templates.TemplateResponse("partials/invite_user_modal.html", {
            "request": request,
            "error": error,
        })

    # Return the modal with success state showing temp password
    return templates.TemplateResponse("partials/invite_user_modal.html", {
        "request": request,
        "temp_password": temp_password,
        "email": email,
    })


@router.patch("/users/{user_id}/role", response_class=HTMLResponse)
async def change_user_role(
    request: Request,
    user_id: str,
    is_admin: bool = Form(...),
    admin: TokenData = Depends(require_admin),
):
    """Change a user's role (admin only)."""
    current_user = db.get_user_by_id(admin.user_id)

    # Prevent self-role-change
    if user_id == admin.user_id:
        users = db.list_users()
        return templates.TemplateResponse("partials/account_tab.html", {
            "request": request,
            "current_user": current_user,
            "users": users,
            "is_admin": True,
            "error": "Cannot change your own role",
        })

    success, error = auth.change_user_role(user_id, is_admin)
    if not success:
        users = db.list_users()
        return templates.TemplateResponse("partials/account_tab.html", {
            "request": request,
            "current_user": current_user,
            "users": users,
            "is_admin": True,
            "error": error,
        })

    # Return updated users tab
    users = db.list_users()
    target_user = db.get_user_by_id(user_id)
    role_action = "promoted to admin" if is_admin else "demoted to user"
    return templates.TemplateResponse("partials/account_tab.html", {
        "request": request,
        "current_user": current_user,
        "users": users,
        "is_admin": True,
        "success": f"{target_user['email']} {role_action}",
    })


@router.delete("/users/{user_id}", response_class=HTMLResponse)
async def delete_user(
    request: Request,
    user_id: str,
    admin: TokenData = Depends(require_admin),
):
    """Delete a user (admin only)."""
    current_user = db.get_user_by_id(admin.user_id)

    # Prevent self-deletion
    if user_id == admin.user_id:
        users = db.list_users()
        return templates.TemplateResponse("partials/account_tab.html", {
            "request": request,
            "current_user": current_user,
            "users": users,
            "is_admin": True,
            "error": "Cannot delete your own account",
        })

    # Revoke all sessions first
    db.revoke_all_user_sessions(user_id)

    if not db.delete_user(user_id):
        users = db.list_users()
        return templates.TemplateResponse("partials/account_tab.html", {
            "request": request,
            "current_user": current_user,
            "users": users,
            "is_admin": True,
            "error": "User not found",
        })

    # Return updated users tab
    users = db.list_users()
    return templates.TemplateResponse("partials/account_tab.html", {
        "request": request,
        "current_user": current_user,
        "users": users,
        "is_admin": True,
        "success": "User deleted successfully",
    })


# ============== Session Management ==============

@router.post("/logout-all")
async def logout_all_sessions(current_user: TokenData = Depends(get_current_user)):
    """Logout from all sessions."""
    count = auth.logout_all_sessions(current_user.user_id)
    return {"status": "success", "sessions_revoked": count}
