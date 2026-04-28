"""Integration tests for UserSettingsRepository.

Uses the shared `postgres_container` session-scoped fixture.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base, User
from db.repositories.settings import UserSettingsRepository

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


@pytest_asyncio.fixture
async def db_session(postgres_container: str) -> AsyncIterator[AsyncSession]:
    """Fresh AsyncSession per test; truncates user_settings + users after each."""
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
        for tbl in ("user_settings", "user_sessions", "users"):
            await session.execute(Base.metadata.tables[tbl].delete())  # type: ignore[arg-type]
        await session.commit()
    await engine.dispose()


async def _create_user(session: AsyncSession) -> uuid.UUID:
    user = User(
        email=f"test-{uuid.uuid4()}@example.com",
        password_hash="hashed",
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user.id


@pytest.mark.asyncio
async def test_get_returns_none_when_unset(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = UserSettingsRepository(db_session)
    assert await repo.get(user_id, "api_host") is None


@pytest.mark.asyncio
async def test_upsert_then_get_round_trip(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = UserSettingsRepository(db_session)
    await repo.upsert(user_id, "api_host", "https://api.lucidlink.com/api/v1")
    await db_session.commit()
    assert await repo.get(user_id, "api_host") == "https://api.lucidlink.com/api/v1"


@pytest.mark.asyncio
async def test_upsert_replaces_existing(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = UserSettingsRepository(db_session)
    await repo.upsert(user_id, "api_host", "https://old.example.com")
    await db_session.commit()
    await repo.upsert(user_id, "api_host", "https://new.example.com")
    await db_session.commit()
    assert await repo.get(user_id, "api_host") == "https://new.example.com"


@pytest.mark.asyncio
async def test_user_isolation(db_session: AsyncSession) -> None:
    """One user's setting must not be visible to another."""
    user_a = await _create_user(db_session)
    user_b = await _create_user(db_session)
    repo = UserSettingsRepository(db_session)
    await repo.upsert(user_a, "api_host", "host-a")
    await repo.upsert(user_b, "api_host", "host-b")
    await db_session.commit()
    assert await repo.get(user_a, "api_host") == "host-a"
    assert await repo.get(user_b, "api_host") == "host-b"


@pytest.mark.asyncio
async def test_delete_removes_row(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = UserSettingsRepository(db_session)
    await repo.upsert(user_id, "api_host", "x")
    await db_session.commit()

    deleted = await repo.delete(user_id, "api_host")
    await db_session.commit()
    assert deleted is True
    assert await repo.get(user_id, "api_host") is None


@pytest.mark.asyncio
async def test_delete_returns_false_when_unset(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = UserSettingsRepository(db_session)
    deleted = await repo.delete(user_id, "api_host")
    await db_session.commit()
    assert deleted is False


@pytest.mark.asyncio
async def test_cascade_delete_on_user_drop(db_session: AsyncSession) -> None:
    """ON DELETE CASCADE on user_id cleans up settings when the user is removed."""
    user_id = await _create_user(db_session)
    repo = UserSettingsRepository(db_session)
    await repo.upsert(user_id, "api_host", "x")
    await db_session.commit()

    await db_session.execute(
        Base.metadata.tables["users"]
        .delete()
        .where(  # type: ignore[arg-type]
            Base.metadata.tables["users"].c.id == user_id  # type: ignore[arg-type]
        )
    )
    await db_session.commit()

    assert await repo.get(user_id, "api_host") is None
