"""Integration tests for app/services/database.py.

Requires a reachable Postgres instance via DATABASE_URL.  The test module
skips itself when DATABASE_URL is absent so CI without a DB tier stays green.
"""

import os

import pytest
import pytest_asyncio  # noqa: F401 — needed for pytest-asyncio fixture wiring

if not os.environ.get("DATABASE_URL"):
    pytest.skip(
        "DATABASE_URL not set — skipping Postgres integration tests",
        allow_module_level=True,
    )

from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

# Reset module-level singletons before each test to keep tests independent.
import services.database as db_module


@pytest.fixture(autouse=True)
def reset_singletons() -> None:
    """Restore singleton state between tests."""
    db_module._engine = None
    db_module._sessionmaker = None
    yield  # type: ignore[misc]
    db_module._engine = None
    db_module._sessionmaker = None


# ── Test 1: get_engine() returns AsyncEngine; singleton ──────────────────────


def test_get_engine_is_async_engine_and_singleton() -> None:
    engine1 = db_module.get_engine()
    engine2 = db_module.get_engine()
    assert isinstance(engine1, AsyncEngine)
    assert engine1 is engine2


# ── Test 2: session executes SELECT 1 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_select_1() -> None:
    session_factory = db_module.get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(text("SELECT 1"))
        row = result.scalar_one()
    assert row == 1

    await db_module.shutdown_engine()


# ── Test 3: pool exhaustion raises TimeoutError quickly ──────────────────────


@pytest.mark.asyncio
async def test_pool_exhaustion_raises_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold one connection then try to acquire a second with pool=1/overflow=0/timeout=1s."""
    monkeypatch.setenv("DB_POOL_SIZE", "1")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "1")

    # Singletons already cleared by autouse fixture; new env vars take effect.
    session_factory = db_module.get_sessionmaker()

    # Keep the first connection open throughout the timeout attempt.
    holder: AsyncSession = session_factory()
    await holder.execute(text("SELECT 1"))

    with pytest.raises((SATimeoutError, TimeoutError)):
        second = session_factory()
        await second.execute(text("SELECT 1"))
        await second.close()

    await holder.close()
    await db_module.shutdown_engine()


# ── Test 4: shutdown_engine() is idempotent ───────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_engine_idempotent() -> None:
    db_module.get_engine()
    await db_module.shutdown_engine()
    await db_module.shutdown_engine()  # second call must not raise
