"""Integration tests for services/idempotency.py.

Tests use the postgres_container fixture from conftest.py (testcontainers
Postgres with Alembic migrations applied). Each test synthesises a minimal
ARQ ctx dict with a sessionmaker backed by the container.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base, ProcessedJob
from services.idempotency import idempotent

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sm(
    postgres_container: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test async_sessionmaker; truncates processed_jobs after each test."""
    engine: AsyncEngine = create_async_engine(
        postgres_container,
        connect_args={"ssl": False},
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    yield factory
    async with factory() as session:
        await session.execute(Base.metadata.tables["processed_jobs"].delete())  # type: ignore[arg-type]
        await session.commit()
    await engine.dispose()


def _ctx(job_id: str, sm: async_sessionmaker[AsyncSession]) -> dict:  # type: ignore[type-arg]
    return {"job_id": job_id, "sessionmaker": sm}


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_processed_row(
    sm: async_sessionmaker[AsyncSession], job_id: str
) -> ProcessedJob | None:
    async with sm() as session:
        result = await session.execute(
            select(ProcessedJob).where(ProcessedJob.job_id == job_id)
        )
        return result.scalar_one_or_none()


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_invocation_runs_function(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """First call with a given job_id executes the wrapped function."""
    counter = 0

    @idempotent
    async def task(ctx: dict, value: int = 1) -> int:  # type: ignore[type-arg]
        nonlocal counter
        counter += value
        return counter

    result = await task(_ctx("job-first-1", sm), value=1)

    assert counter == 1
    assert result == 1

    row = await _get_processed_row(sm, "job-first-1")
    assert row is not None
    assert row.result_status is None


@pytest.mark.asyncio
async def test_second_invocation_is_noop(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """Second call with the same job_id is a no-op; returns None."""
    counter = 0

    @idempotent
    async def task(ctx: dict) -> int:  # type: ignore[type-arg]
        nonlocal counter
        counter += 1
        return counter

    ctx = _ctx("job-noop-1", sm)
    first = await task(ctx)
    second = await task(ctx)

    assert first == 1
    assert counter == 1
    assert second is None


@pytest.mark.asyncio
async def test_different_job_ids_both_run(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """Two calls with different job_ids each execute the wrapped function."""
    counter = 0

    @idempotent
    async def task(ctx: dict) -> int:  # type: ignore[type-arg]
        nonlocal counter
        counter += 1
        return counter

    await task(_ctx("job-diff-1", sm))
    await task(_ctx("job-diff-2", sm))

    assert counter == 2


@pytest.mark.asyncio
async def test_missing_job_id_runs_normally(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """ctx without job_id executes the function without recording a DB row."""
    counter = 0

    @idempotent
    async def task(ctx: dict) -> int:  # type: ignore[type-arg]
        nonlocal counter
        counter += 1
        return counter

    # No job_id in ctx — runs normally, no processed_jobs row inserted.
    result = await task({"sessionmaker": sm})

    assert counter == 1
    assert result == 1

    async with sm() as session:
        total = await session.execute(select(ProcessedJob))
        rows = total.scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_failed_task_marks_result_status_failed(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """Failed wrapped function sets result_status='failed'; re-delivery still skips.

    Contract: the dedup insert happens BEFORE the function body runs. A failure
    updates the already-inserted row to result_status='failed' but does not
    remove the row. A second delivery with the same job_id finds the existing
    row (ON CONFLICT DO NOTHING → 0 affected rows) and skips — even though the
    first attempt failed. This prevents duplicate LucidLink imports on transient
    worker crashes; operators must requeue explicitly.
    """
    counter = 0

    @idempotent
    async def failing_task(ctx: dict) -> None:  # type: ignore[type-arg]
        nonlocal counter
        counter += 1
        raise ValueError("intentional failure")

    ctx = _ctx("job-fail-1", sm)

    with pytest.raises(ValueError, match="intentional failure"):
        await failing_task(ctx)

    assert counter == 1

    row = await _get_processed_row(sm, "job-fail-1")
    assert row is not None
    assert row.result_status == "failed"

    # Second delivery with the same job_id skips (returns None, no exception).
    result = await failing_task(ctx)
    assert result is None
    assert counter == 1  # body did NOT run again


@pytest.mark.asyncio
async def test_uses_ctx_sessionmaker_when_present(
    sm: async_sessionmaker[AsyncSession],
    postgres_container: str,
) -> None:
    """Decorator uses ctx['sessionmaker'] and not the global get_sessionmaker().

    We verify this by supplying a second sessionmaker backed by the same
    container (same schema) and asserting that the processed_jobs row appears
    — meaning the decorator wrote through the sessionmaker we injected via ctx,
    not some other one.
    """
    engine2: AsyncEngine = create_async_engine(
        postgres_container,
        connect_args={"ssl": False},
    )
    sm2: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine2, expire_on_commit=False
    )

    counter = 0

    @idempotent
    async def task(ctx: dict) -> int:  # type: ignore[type-arg]
        nonlocal counter
        counter += 1
        return counter

    # Pass sm2 explicitly in ctx.
    await task({"job_id": "job-ctx-sm-1", "sessionmaker": sm2})

    assert counter == 1

    # Row must be visible through the original sm (same Postgres DB).
    row = await _get_processed_row(sm, "job-ctx-sm-1")
    assert row is not None

    await engine2.dispose()
