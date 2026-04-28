"""Integration tests for JobQueue using a real Postgres container.

Each test constructs a JobQueue instance, calls set_sessionmaker(), and
exercises the public async methods without going through main.py or FastAPI.
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
from services.job_queue import JobQueue

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sm(
    postgres_container: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Provide a per-test async_sessionmaker backed by the shared Postgres container."""
    engine: AsyncEngine = create_async_engine(
        postgres_container,
        connect_args={"ssl": False},
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    yield factory
    # Cleanup: truncate tables used by these tests.
    async with factory() as session:
        for tbl in ("processed_jobs", "import_jobs", "user_sessions", "users"):
            await session.execute(Base.metadata.tables[tbl].delete())  # type: ignore[arg-type]
        await session.commit()
    await engine.dispose()


@pytest_asyncio.fixture
async def queue(
    sm: async_sessionmaker[AsyncSession],
) -> JobQueue:
    """Return a JobQueue with an injected sessionmaker (no Valkey required)."""
    q = JobQueue()
    q.set_sessionmaker(sm)
    return q


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_user(sm: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    async with sm() as session:
        user = User(
            email=f"test-{uuid.uuid4()}@example.com",
            password_hash="hashed",
        )
        session.add(user)
        await session.flush()
        await session.refresh(user)
        uid: uuid.UUID = user.id
        await session.commit()
    return uid


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_job_returns_int_id(
    queue: JobQueue, sm: async_sessionmaker[AsyncSession]
) -> None:
    """add_job creates a DB row and returns a positive integer job ID."""
    user_id = await _make_user(sm)

    job_id = await queue.add_job(
        bucket="test-bucket",
        prefix="folder/",
        filespace_id="fs-001",
        datastore_id="ds-001",
        user_id=str(user_id),
    )

    assert isinstance(job_id, int)
    assert job_id > 0


@pytest.mark.asyncio
async def test_cancel_job_happy_path(
    queue: JobQueue, sm: async_sessionmaker[AsyncSession]
) -> None:
    """cancel_job returns True for the owning user and the job status becomes 'cancelled'."""
    user_id = await _make_user(sm)

    job_id = await queue.add_job(
        bucket="bucket",
        prefix="prefix/",
        filespace_id="fs",
        datastore_id="ds",
        user_id=str(user_id),
    )

    result = await queue.cancel_job(job_id, user_id=str(user_id))
    assert result is True

    jobs = await queue.get_jobs(user_id=str(user_id))
    matching = [j for j in jobs if j["id"] == job_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_get_jobs_filters_by_user(
    queue: JobQueue, sm: async_sessionmaker[AsyncSession]
) -> None:
    """get_jobs returns only jobs for the requested user."""
    user_a = await _make_user(sm)
    user_b = await _make_user(sm)

    for i in range(2):
        await queue.add_job(
            bucket="bucket",
            prefix=f"a/{i}/",
            filespace_id="fs",
            datastore_id="ds",
            user_id=str(user_a),
        )

    await queue.add_job(
        bucket="bucket",
        prefix="b/0/",
        filespace_id="fs",
        datastore_id="ds",
        user_id=str(user_b),
    )

    jobs_a = await queue.get_jobs(user_id=str(user_a))
    jobs_b = await queue.get_jobs(user_id=str(user_b))

    assert len(jobs_a) == 2
    assert len(jobs_b) == 1
    assert all(j["user_id"] == str(user_a) for j in jobs_a)
    assert all(j["user_id"] == str(user_b) for j in jobs_b)


@pytest.mark.asyncio
async def test_get_queue_status_counts(
    queue: JobQueue, sm: async_sessionmaker[AsyncSession]
) -> None:
    """get_queue_status returns correct pending/running counts and connected=False (no Valkey)."""
    user_id = await _make_user(sm)

    await queue.add_job(
        bucket="bucket",
        prefix="prefix-1/",
        filespace_id="fs",
        datastore_id="ds",
        user_id=str(user_id),
    )
    await queue.add_job(
        bucket="bucket",
        prefix="prefix-2/",
        filespace_id="fs",
        datastore_id="ds",
        user_id=str(user_id),
    )

    status = await queue.get_queue_status(user_id=str(user_id))

    assert status["pending"] == 2
    assert status["running"] == 0
    assert status["connected"] is False
