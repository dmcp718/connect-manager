"""
Authentication Service
JWT token management and password hashing
"""

import os
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from services import database as db


# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))  # 8 hours default


class AuthError(Exception):
    """Authentication error."""
    pass


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> Tuple[bool, str]:
    """
    Validate password meets minimum requirements.
    Returns (is_valid, error_message).
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"
    return True, ""


def create_access_token(user_id: str, email: str, is_admin: bool = False) -> str:
    """
    Create a JWT access token.
    Also creates a session record in the database for revocation support.
    """
    session_id = str(uuid4())
    expires_at = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    # Store session in database
    db.create_session(user_id, session_id, expires_at)

    # Create JWT
    payload = {
        "sub": user_id,
        "email": email,
        "is_admin": is_admin,
        "jti": session_id,
        "exp": expires_at,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT token.
    Returns the payload if valid, None otherwise.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        # Check if session is still valid (not revoked)
        session_id = payload.get("jti")
        if session_id:
            session = db.get_session(session_id)
            if not session:
                return None  # Session revoked or expired

        return payload
    except JWTError:
        return None


def authenticate_user(email: str, password: str) -> Tuple[Optional[dict], str]:
    """
    Authenticate a user with email and password.
    Returns (user_dict, error_message).
    """
    user = db.get_user_by_email(email)
    if not user:
        return None, "Invalid email or password"

    if not verify_password(password, user["password_hash"]):
        return None, "Invalid email or password"

    # Update last login
    db.update_user_last_login(user["id"])

    return user, ""


def register_user(
    email: str,
    password: str,
    display_name: Optional[str] = None,
    is_admin: bool = False,
) -> Tuple[Optional[str], str]:
    """
    Register a new user.
    Returns (user_id, error_message).
    """
    # Validate email format
    if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email):
        return None, "Invalid email format"

    # Check if email domain is restricted
    allowed_domain = os.getenv("ALLOWED_EMAIL_DOMAIN")
    if allowed_domain and not email.lower().endswith(f"@{allowed_domain.lower()}"):
        return None, f"Only @{allowed_domain} email addresses are allowed"

    # Validate password strength
    is_valid, error_msg = validate_password_strength(password)
    if not is_valid:
        return None, error_msg

    # Check if user already exists
    existing = db.get_user_by_email(email)
    if existing:
        return None, "Email already registered"

    # Create user
    password_hash = hash_password(password)
    try:
        user_id = db.create_user(email, password_hash, display_name, is_admin)
        return user_id, ""
    except Exception as e:
        return None, f"Failed to create user: {e}"


def logout_user(session_id: str) -> bool:
    """Revoke a user's session."""
    return db.revoke_session(session_id)


def logout_all_sessions(user_id: str) -> int:
    """Revoke all sessions for a user."""
    return db.revoke_all_user_sessions(user_id)


def change_password(user_id: str, old_password: str, new_password: str) -> Tuple[bool, str]:
    """
    Change a user's password.
    Returns (success, error_message).
    """
    user = db.get_user_by_id(user_id)
    if not user:
        return False, "User not found"

    if not verify_password(old_password, user["password_hash"]):
        return False, "Current password is incorrect"

    is_valid, error_msg = validate_password_strength(new_password)
    if not is_valid:
        return False, error_msg

    new_hash = hash_password(new_password)
    if db.update_user_password(user_id, new_hash):
        # Revoke all existing sessions
        db.revoke_all_user_sessions(user_id)
        return True, ""
    return False, "Failed to update password"


def create_invite_user(email: str, is_admin: bool = False) -> Tuple[Optional[str], str, str]:
    """
    Create a user with a temporary password (for invite flow).
    Returns (user_id, temp_password, error_message).
    """
    import secrets
    temp_password = secrets.token_urlsafe(12)

    user_id, error = register_user(email, temp_password, is_admin=is_admin)
    if error:
        return None, "", error

    return user_id, temp_password, ""


def change_user_role(user_id: str, is_admin: bool) -> Tuple[bool, str]:
    """
    Change a user's role. Revokes all sessions to force re-auth with new role.
    Returns (success, error_message).
    """
    user = db.get_user_by_id(user_id)
    if not user:
        return False, "User not found"

    # Update role in database
    if not db.update_user_role(user_id, is_admin):
        return False, "Failed to update user role"

    # Revoke all user sessions to force re-login with new role claims
    db.revoke_all_user_sessions(user_id)

    return True, ""


def ensure_admin_exists() -> None:
    """
    Ensure at least one admin user exists (for first-time setup).

    For production, set environment variables:
      ADMIN_EMAIL=admin@example.com
      ADMIN_PASSWORD=secure-password-here

    If not set, defaults to admin@localhost / admin (dev only).
    """
    if db.count_users() == 0:
        # Get admin credentials from environment or use defaults
        admin_email = os.getenv("ADMIN_EMAIL", "admin@localhost")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin")

        password_hash = hash_password(admin_password)
        db.create_user(
            email=admin_email,
            password_hash=password_hash,
            display_name="Admin",
            is_admin=True
        )

        # Log warning if using default credentials
        if admin_email == "admin@localhost" and admin_password == "admin":
            import logging
            logging.warning("Using default admin credentials - change immediately in production!")
