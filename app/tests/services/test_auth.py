"""Integration tests for app/services/auth.py against a live Postgres container.

Each test builds its own engine + session so state cannot leak between them.
The shared ``postgres_container`` fixture (session-scoped) runs Alembic
``upgrade head`` once before any test in the session runs.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base
from services import auth

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


# ── Per-test session fixture ──────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session(postgres_container: str) -> AsyncIterator[AsyncSession]:
    """Fresh session per test; rolls back and truncates users + user_sessions."""
    engine: AsyncEngine = create_async_engine(
        postgres_container,
        connect_args={"ssl": False},
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as session:
        yield session
        await session.rollback()
        # Clean in FK-safe order.
        for tbl in ("user_sessions", "users"):
            await session.execute(Base.metadata.tables[tbl].delete())  # type: ignore[arg-type]
        await session.commit()
    await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────────


_EMAIL = "test@example.com"
_PASSWORD = "StrongPass1"


async def _register(session: AsyncSession, **kwargs: object) -> dict[str, object]:
    email = str(kwargs.get("email", _EMAIL))
    password = str(kwargs.get("password", _PASSWORD))
    display_name = kwargs.get("display_name", None)
    is_admin = bool(kwargs.get("is_admin", False))
    user_dict, error = await auth.register_user(
        session,
        email,
        password,
        display_name=display_name,
        is_admin=is_admin,  # type: ignore[arg-type]
    )
    assert error == "", f"register_user failed: {error}"
    assert user_dict is not None
    return user_dict


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_creates_user(db_session: AsyncSession) -> None:
    """Registering a new user creates a row; password round-trips correctly."""
    from db.repositories.user import UserRepository

    user_dict = await _register(db_session)
    repo = UserRepository(db_session)
    row = await repo.get(uuid.UUID(str(user_dict["id"])))
    assert row is not None
    assert row.email == _EMAIL
    assert auth.verify_password(_PASSWORD, row.password_hash)


@pytest.mark.asyncio
async def test_register_rejects_duplicate_email(db_session: AsyncSession) -> None:
    """Registering the same email twice returns an error on the second call."""
    await _register(db_session)
    _, error = await auth.register_user(db_session, _EMAIL, _PASSWORD)
    assert error == "Email already registered"


@pytest.mark.asyncio
async def test_authenticate_valid_credentials(db_session: AsyncSession) -> None:
    """Authenticate with correct credentials returns user dict + empty error."""
    await _register(db_session)
    user, error = await auth.authenticate_user(db_session, _EMAIL, _PASSWORD)
    assert error == ""
    assert user is not None
    assert user["email"] == _EMAIL


@pytest.mark.asyncio
async def test_authenticate_wrong_password(db_session: AsyncSession) -> None:
    """Wrong password returns None + non-empty error string."""
    await _register(db_session)
    user, error = await auth.authenticate_user(db_session, _EMAIL, "WrongPass9")
    assert user is None
    assert error != ""


@pytest.mark.asyncio
async def test_authenticate_unknown_email(db_session: AsyncSession) -> None:
    """Unknown email returns None + non-empty error string."""
    user, error = await auth.authenticate_user(
        db_session, "nobody@example.com", _PASSWORD
    )
    assert user is None
    assert error != ""


@pytest.mark.asyncio
async def test_create_and_decode_token_round_trip(db_session: AsyncSession) -> None:
    """Issue a token then decode it; claims must match the registered user."""
    user_dict = await _register(db_session)
    token = await auth.create_access_token(
        db_session,
        user_id=str(user_dict["id"]),
        email=str(user_dict["email"]),
        is_admin=False,
    )
    payload = await auth.decode_token(db_session, token)
    assert payload is not None
    assert payload["sub"] == str(user_dict["id"])
    assert payload["email"] == _EMAIL


@pytest.mark.asyncio
async def test_decode_token_after_logout(db_session: AsyncSession) -> None:
    """After logout the session is revoked; decode_token must return None."""
    user_dict = await _register(db_session)
    token = await auth.create_access_token(
        db_session,
        user_id=str(user_dict["id"]),
        email=str(user_dict["email"]),
    )
    payload = await auth.decode_token(db_session, token)
    assert payload is not None
    session_id = str(payload["jti"])
    revoked = await auth.logout_user(db_session, session_id)
    assert revoked is True
    gone = await auth.decode_token(db_session, token)
    assert gone is None


@pytest.mark.asyncio
async def test_change_password_invalidates_old_hash(db_session: AsyncSession) -> None:
    """After change_password the old password no longer authenticates."""
    user_dict = await _register(db_session)
    new_pw = "NewStrongPass2"
    success, error = await auth.change_password(
        db_session, str(user_dict["id"]), _PASSWORD, new_pw
    )
    assert success is True, error

    user, err = await auth.authenticate_user(db_session, _EMAIL, _PASSWORD)
    assert user is None
    assert err != ""

    user2, err2 = await auth.authenticate_user(db_session, _EMAIL, new_pw)
    assert err2 == ""
    assert user2 is not None


@pytest.mark.asyncio
async def test_password_hash_format_compat_with_main(db_session: AsyncSession) -> None:
    """A bcrypt hash produced by passlib matches verify_password — proves migration compat."""
    _compat_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    known_hash = _compat_ctx.hash(_PASSWORD)
    assert auth.verify_password(_PASSWORD, known_hash) is True
    assert auth.verify_password("WrongPass9", known_hash) is False
