"""Authentication Service — JWT token management and password hashing.

All public functions that touch the database now accept an AsyncSession as
their first argument and are async.  Pure crypto/validation helpers stay sync.

Note: ensure_admin_exists() and create_access_token() are now async; callers
in main.py lifespan and routes must be updated accordingly.  The lifespan
wiring in app/main.py still calls the old sync signatures and will raise at
startup until awsk-opc rewires it.
"""

from __future__ import annotations

import logging
import os
import re
import secrets as _secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from uuid import UUID, uuid4

from jose import JWTError, jwt  # type: ignore[import-untyped]
from passlib.context import CryptContext  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.user import UserRepository
from db.repositories.user_session import UserSessionRepository

# Password hashing — scheme is UNCHANGED from main for hash compatibility.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

_WEAK_SECRETS = {
    "dev-secret-key-change-in-production",
    "change-me-in-production",
    "secret",
    "jwt-secret",
    "your-secret-key",
}


# ── Pure sync helpers (unchanged) ────────────────────────────────────────────


def validate_jwt_secret() -> tuple[bool, str]:
    """Validate JWT_SECRET_KEY is secure. Returns (is_valid, error_message)."""
    secret = os.getenv("JWT_SECRET_KEY", "")
    if not secret:
        return False, "JWT_SECRET_KEY environment variable is not set"
    if (
        secret.lower() in _WEAK_SECRETS
        or secret == "dev-secret-key-change-in-production"
    ):
        return (
            False,
            "JWT_SECRET_KEY is using a known default value - generate with: openssl rand -hex 32",
        )
    if len(secret) < 32:
        return (
            False,
            "JWT_SECRET_KEY is too short - should be at least 32 characters (recommend 64 hex chars)",
        )
    return True, ""


def ensure_jwt_secret_valid() -> None:
    """Validate JWT secret at startup. Blocks in production, warns in development."""
    is_valid, error = validate_jwt_secret()
    is_production = (
        bool(os.getenv("DOMAIN"))
        or os.getenv("ENVIRONMENT", "").lower() == "production"
    )
    if not is_valid:
        if is_production:
            raise RuntimeError(f"SECURITY ERROR: {error}")
        else:
            logging.warning(f"SECURITY WARNING: {error}")
            logging.warning(
                "This is acceptable for development but MUST be fixed before production deployment."
            )


class AuthError(Exception):
    """Authentication error."""


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return str(pwd_context.hash(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return bool(pwd_context.verify(plain_password, hashed_password))


def validate_password_strength(password: str) -> Tuple[bool, str]:
    """Validate password meets minimum requirements. Returns (is_valid, error_message)."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"
    return True, ""


# ── Async DB-touching functions ───────────────────────────────────────────────


def _user_to_dict(user: object) -> dict[str, object]:
    """Serialize a User ORM row to a plain dict matching the old db.get_user_by_* shape."""
    from db.models import User as UserModel

    assert isinstance(user, UserModel)
    return {
        "id": str(user.id),
        "email": user.email,
        "password_hash": user.password_hash,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "last_login": user.last_login,
    }


async def authenticate_user(
    session: AsyncSession, email: str, password: str
) -> tuple[Optional[dict[str, object]], str]:
    """Authenticate a user with email and password. Returns (user_dict, error_message)."""
    repo = UserRepository(session)
    user = await repo.get_by_email(email)
    if not user:
        return None, "Invalid email or password"
    if not verify_password(password, user.password_hash):
        return None, "Invalid email or password"
    await repo.update_last_login(user.id)
    await session.commit()
    await session.refresh(user)
    return _user_to_dict(user), ""


async def register_user(
    session: AsyncSession,
    email: str,
    password: str,
    display_name: Optional[str] = None,
    is_admin: bool = False,
) -> tuple[Optional[dict[str, object]], str]:
    """Register a new user. Returns (user_dict, error_message)."""
    if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email):
        return None, "Invalid email format"
    allowed_domain = os.getenv("ALLOWED_EMAIL_DOMAIN")
    if allowed_domain and not email.lower().endswith(f"@{allowed_domain.lower()}"):
        return None, f"Only @{allowed_domain} email addresses are allowed"
    is_valid, error_msg = validate_password_strength(password)
    if not is_valid:
        return None, error_msg
    repo = UserRepository(session)
    existing = await repo.get_by_email(email)
    if existing:
        return None, "Email already registered"
    password_hash = hash_password(password)
    try:
        user = await repo.create(
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            is_admin=is_admin,
        )
        await session.commit()
        return _user_to_dict(user), ""
    except Exception as exc:
        return None, f"Failed to create user: {exc}"


async def create_access_token(
    session: AsyncSession,
    user_id: str,
    email: str,
    is_admin: bool = False,
) -> str:
    """Create a JWT access token and persist a session record."""
    session_id = uuid4()
    expires_at = datetime.now(tz=timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )
    sess_repo = UserSessionRepository(session)
    await sess_repo.create_session(
        user_id=UUID(user_id),
        session_id=session_id,
        expires_at=expires_at,
    )
    await session.commit()
    payload = {
        "sub": user_id,
        "email": email,
        "is_admin": is_admin,
        "jti": str(session_id),
        "exp": expires_at,
        "iat": datetime.now(tz=timezone.utc),
    }
    return str(jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM))


async def decode_token(
    session: AsyncSession, token: str
) -> Optional[dict[str, object]]:
    """Decode and validate a JWT token. Returns payload if valid, None otherwise."""
    try:
        payload: dict[str, object] = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM]
        )
    except JWTError:
        return None
    raw_jti = payload.get("jti")
    if raw_jti:
        sess_repo = UserSessionRepository(session)
        active = await sess_repo.get_active_session(UUID(str(raw_jti)))
        if not active:
            return None
    return payload


async def logout_user(session: AsyncSession, session_id: str) -> bool:
    """Revoke a user's session."""
    sess_repo = UserSessionRepository(session)
    revoked = await sess_repo.revoke_session(UUID(session_id))
    await session.commit()
    return revoked


async def logout_all_sessions(session: AsyncSession, user_id: str) -> int:
    """Revoke all sessions for a user."""
    sess_repo = UserSessionRepository(session)
    count = await sess_repo.revoke_all_user_sessions(UUID(user_id))
    await session.commit()
    return count


async def change_password(
    session: AsyncSession,
    user_id: str,
    old_password: str,
    new_password: str,
) -> tuple[bool, str]:
    """Change a user's password. Returns (success, error_message)."""
    repo = UserRepository(session)
    user = await repo.get(UUID(user_id))
    if not user:
        return False, "User not found"
    if not verify_password(old_password, user.password_hash):
        return False, "Current password is incorrect"
    is_valid, error_msg = validate_password_strength(new_password)
    if not is_valid:
        return False, error_msg
    new_hash = hash_password(new_password)
    updated = await repo.update_password(UUID(user_id), new_hash)
    if not updated:
        return False, "Failed to update password"
    sess_repo = UserSessionRepository(session)
    await sess_repo.revoke_all_user_sessions(UUID(user_id))
    await session.commit()
    return True, ""


async def create_invite_user(
    session: AsyncSession,
    email: str,
    is_admin: bool = False,
) -> tuple[Optional[str], str, str]:
    """Create a user with a temporary password. Returns (user_id, temp_password, error_message)."""
    temp_password = _secrets.token_urlsafe(12)
    user_dict, error = await register_user(
        session, email, temp_password, is_admin=is_admin
    )
    if error:
        return None, "", error
    assert user_dict is not None
    return str(user_dict["id"]), temp_password, ""


async def change_user_role(
    session: AsyncSession,
    user_id: str,
    is_admin: bool,
) -> tuple[bool, str]:
    """Change a user's role. Revokes all sessions to force re-auth. Returns (success, error_message)."""
    repo = UserRepository(session)
    updated = await repo.update(UUID(user_id), is_admin=is_admin)
    if updated is None:
        return False, "User not found"
    sess_repo = UserSessionRepository(session)
    await sess_repo.revoke_all_user_sessions(UUID(user_id))
    await session.commit()
    return True, ""


async def ensure_admin_exists(session: AsyncSession) -> None:
    """Ensure at least one admin user exists (for first-time setup).

    Caller in main.py lifespan must be updated to pass the session and await
    this coroutine — tracked in awsk-opc.
    """
    repo = UserRepository(session)
    count = await repo.count()
    if count == 0:
        admin_email = os.getenv("ADMIN_EMAIL", "admin@localhost")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin")
        password_hash = hash_password(admin_password)
        await repo.create(
            email=admin_email,
            password_hash=password_hash,
            display_name="Admin",
            is_admin=True,
        )
        await session.commit()
        if admin_email == "admin@localhost" and admin_password == "admin":
            logging.warning(
                "Using default admin credentials - change immediately in production!"
            )
