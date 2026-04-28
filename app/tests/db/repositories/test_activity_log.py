"""Integration tests for ActivityLogRepository.

Uses the shared `postgres_container` session-scoped fixture.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base, User
from db.repositories.activity_log import ActivityLogRepository

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


@pytest_asyncio.fixture
async def db_session(postgres_container: str) -> AsyncIterator[AsyncSession]:
    """Fresh AsyncSession per test; truncates activity_logs + users after each."""
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
        for tbl in ("activity_logs", "user_sessions", "users"):
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
async def test_create_then_list(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = ActivityLogRepository(db_session)

    row = await repo.create_entry(
        category="app",
        action="connection",
        message="hello",
        level="info",
        user_id=user_id,
        details={"foo": "bar"},
    )
    await db_session.commit()
    assert row.id > 0

    rows = await repo.list_filtered(user_id=user_id)
    assert len(rows) == 1
    assert rows[0].message == "hello"
    assert rows[0].details == {"foo": "bar"}


@pytest.mark.asyncio
async def test_list_filters_by_category(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = ActivityLogRepository(db_session)
    await repo.create_entry(category="app", action="x", message="m1", user_id=user_id)
    await repo.create_entry(category="job", action="x", message="m2", user_id=user_id)
    await db_session.commit()

    app_rows = await repo.list_filtered(category="app", user_id=user_id)
    job_rows = await repo.list_filtered(category="job", user_id=user_id)
    assert {r.message for r in app_rows} == {"m1"}
    assert {r.message for r in job_rows} == {"m2"}


@pytest.mark.asyncio
async def test_list_isolates_users(db_session: AsyncSession) -> None:
    """Without include_all_users, users only see their own logs."""
    a = await _create_user(db_session)
    b = await _create_user(db_session)
    repo = ActivityLogRepository(db_session)
    await repo.create_entry(category="app", action="x", message="a-msg", user_id=a)
    await repo.create_entry(category="app", action="x", message="b-msg", user_id=b)
    await db_session.commit()

    a_rows = await repo.list_filtered(user_id=a)
    assert {r.message for r in a_rows} == {"a-msg"}


@pytest.mark.asyncio
async def test_list_include_all_users(db_session: AsyncSession) -> None:
    a = await _create_user(db_session)
    b = await _create_user(db_session)
    repo = ActivityLogRepository(db_session)
    await repo.create_entry(category="admin", action="x", message="a-msg", user_id=a)
    await repo.create_entry(category="admin", action="x", message="b-msg", user_id=b)
    await db_session.commit()

    rows = await repo.list_filtered(category="admin", include_all_users=True)
    assert {r.message for r in rows} == {"a-msg", "b-msg"}


@pytest.mark.asyncio
async def test_count_matches_list(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = ActivityLogRepository(db_session)
    for i in range(5):
        await repo.create_entry(
            category="app", action="x", message=f"m{i}", user_id=user_id
        )
    await db_session.commit()

    count = await repo.count_filtered(category="app", user_id=user_id)
    rows = await repo.list_filtered(category="app", user_id=user_id)
    assert count == 5
    assert len(rows) == 5


@pytest.mark.asyncio
async def test_clear_for_user_scoped(db_session: AsyncSession) -> None:
    a = await _create_user(db_session)
    b = await _create_user(db_session)
    repo = ActivityLogRepository(db_session)
    await repo.create_entry(category="app", action="x", message="a", user_id=a)
    await repo.create_entry(category="app", action="x", message="b", user_id=b)
    await db_session.commit()

    deleted = await repo.clear_for_user(category="app", user_id=a)
    await db_session.commit()
    assert deleted == 1
    survivors = await repo.list_filtered(category="app", include_all_users=True)
    assert {r.message for r in survivors} == {"b"}


@pytest.mark.asyncio
async def test_clear_old(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = ActivityLogRepository(db_session)
    fresh = await repo.create_entry(
        category="app", action="x", message="fresh", user_id=user_id
    )
    aged = await repo.create_entry(
        category="app", action="x", message="aged", user_id=user_id
    )
    aged.created_at = datetime.now(tz=timezone.utc) - timedelta(days=60)
    await db_session.commit()

    deleted = await repo.clear_old(days=30)
    await db_session.commit()
    assert deleted == 1
    assert await repo.get(fresh.id) is not None
    assert await repo.get(aged.id) is None


@pytest.mark.asyncio
async def test_user_delete_sets_user_id_null(db_session: AsyncSession) -> None:
    """ON DELETE SET NULL preserves audit history when a user is removed."""
    user_id = await _create_user(db_session)
    repo = ActivityLogRepository(db_session)
    row = await repo.create_entry(
        category="admin", action="login", message="hi", user_id=user_id
    )
    await db_session.commit()

    await db_session.execute(
        Base.metadata.tables["users"]
        .delete()
        .where(  # type: ignore[arg-type]
            Base.metadata.tables["users"].c.id == user_id  # type: ignore[arg-type]
        )
    )
    await db_session.commit()

    refreshed = await repo.get(row.id)
    assert refreshed is not None
    assert refreshed.user_id is None
    assert refreshed.message == "hi"
