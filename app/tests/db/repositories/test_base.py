"""Integration tests for AsyncRepository against the User model.

Requires a live Postgres 16 container (provided by the session-scoped
``postgres_container`` fixture in tests/conftest.py).  Set PYTEST_FAST=1 to
skip this module when iterating quickly on non-DB changes.
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
from db.repositories.base import AsyncRepository

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


@pytest_asyncio.fixture
async def db_session(postgres_container: str) -> AsyncIterator[AsyncSession]:
    """Per-test AsyncSession against the shared Postgres container.

    Rolls back and truncates the users table after each test so that tests
    are fully isolated without the overhead of recreating the schema.
    """
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
        await session.execute(
            Base.metadata.tables["users"].delete()  # type: ignore[arg-type]
        )
        await session.commit()
    await engine.dispose()


def _make_user(**overrides: object) -> dict[str, object]:
    """Return minimal kwargs for User.create() with a unique email."""
    base: dict[str, object] = {
        "email": f"test-{uuid.uuid4()}@example.com",
        "password_hash": "hashed",
        "display_name": "Test User",
        "is_admin": False,
    }
    base.update(overrides)
    return base


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo: AsyncRepository[User, uuid.UUID] = AsyncRepository(db_session, User)
    created = await repo.create(**_make_user(display_name="Alice"))
    assert created.id is not None

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.display_name == "Alice"
    assert fetched.email == created.email


async def test_list_pagination(db_session: AsyncSession) -> None:
    repo: AsyncRepository[User, uuid.UUID] = AsyncRepository(db_session, User)
    created = [await repo.create(**_make_user()) for _ in range(5)]
    await db_session.commit()

    page = await repo.list(limit=2, offset=2, order_by=User.created_at)
    assert len(page) == 2
    # Rows at index 2 and 3 from the ordered set.
    ordered_ids = [r.id for r in sorted(created, key=lambda u: u.created_at)]
    assert page[0].id == ordered_ids[2]
    assert page[1].id == ordered_ids[3]


async def test_update_changes_field(db_session: AsyncSession) -> None:
    repo: AsyncRepository[User, uuid.UUID] = AsyncRepository(db_session, User)
    user = await repo.create(**_make_user(display_name="Before"))
    await db_session.commit()

    updated = await repo.update(user.id, display_name="After")
    assert updated is not None
    assert updated.display_name == "After"

    fresh = await repo.get(user.id)
    assert fresh is not None
    assert fresh.display_name == "After"


async def test_update_returns_none_for_missing(db_session: AsyncSession) -> None:
    repo: AsyncRepository[User, uuid.UUID] = AsyncRepository(db_session, User)
    result = await repo.update(uuid.uuid4(), display_name="Ghost")
    assert result is None


async def test_delete_removes_row(db_session: AsyncSession) -> None:
    repo: AsyncRepository[User, uuid.UUID] = AsyncRepository(db_session, User)
    user = await repo.create(**_make_user())
    await db_session.commit()

    deleted = await repo.delete(user.id)
    assert deleted is True

    gone = await repo.get(user.id)
    assert gone is None


async def test_delete_returns_false_for_missing(db_session: AsyncSession) -> None:
    repo: AsyncRepository[User, uuid.UUID] = AsyncRepository(db_session, User)
    result = await repo.delete(uuid.uuid4())
    assert result is False


async def test_count_and_exists(db_session: AsyncSession) -> None:
    repo: AsyncRepository[User, uuid.UUID] = AsyncRepository(db_session, User)
    initial_count = await repo.count()

    user = await repo.create(**_make_user())
    await db_session.commit()

    assert await repo.count() == initial_count + 1
    assert await repo.exists(user.id) is True
    assert await repo.exists(uuid.uuid4()) is False
