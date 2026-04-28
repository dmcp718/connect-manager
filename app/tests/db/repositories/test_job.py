"""Integration tests for JobRepository and ProcessedJobRepository.

Uses the shared ``postgres_container`` session-scoped fixture which runs
Alembic ``upgrade head`` once per test session.
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
from db.repositories.job import JobRepository, ProcessedJobRepository

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


# ── Per-test session fixture ──────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session(postgres_container: str) -> AsyncIterator[AsyncSession]:
    """Fresh AsyncSession per test; truncates relevant tables after each test."""
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
        for tbl in (
            "processed_jobs",
            "import_jobs",
            "user_sessions",
            "users",
        ):
            await session.execute(Base.metadata.tables[tbl].delete())  # type: ignore[arg-type]
        await session.commit()
    await engine.dispose()


@pytest_asyncio.fixture
async def sessionmaker_fixture(
    postgres_container: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session-level async_sessionmaker for JobQueue-style tests."""
    engine: AsyncEngine = create_async_engine(
        postgres_container,
        connect_args={"ssl": False},
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    yield factory
    await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _create_user(session: AsyncSession) -> uuid.UUID:
    user = User(
        email=f"test-{uuid.uuid4()}@example.com",
        password_hash="hashed",
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user.id


# ── JobRepository tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get(db_session: AsyncSession) -> None:
    """create_job inserts with status='pending'; get returns the same row."""
    user_id = await _create_user(db_session)
    repo = JobRepository(db_session)

    job = await repo.create_job(
        user_id=user_id,
        bucket="my-bucket",
        prefix="folder/",
        filespace_id="fs-001",
        datastore_id="ds-001",
    )
    await db_session.commit()

    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.status == "pending"
    assert fetched.bucket == "my-bucket"
    assert fetched.prefix == "folder/"
    assert fetched.filespace_id == "fs-001"
    assert fetched.datastore_id == "ds-001"
    assert fetched.user_id == user_id


@pytest.mark.asyncio
async def test_list_for_user_filters_correctly(db_session: AsyncSession) -> None:
    """list_for_user returns only jobs belonging to the requested user."""
    user_a = await _create_user(db_session)
    user_b = await _create_user(db_session)
    repo = JobRepository(db_session)

    for i in range(3):
        await repo.create_job(
            user_id=user_a,
            bucket=f"bucket-a-{i}",
            prefix=f"a/{i}/",
            filespace_id="fs-a",
            datastore_id="ds-a",
        )
    for i in range(2):
        await repo.create_job(
            user_id=user_b,
            bucket=f"bucket-b-{i}",
            prefix=f"b/{i}/",
            filespace_id="fs-b",
            datastore_id="ds-b",
        )
    await db_session.commit()

    rows_a = await repo.list_for_user(user_a)
    rows_b = await repo.list_for_user(user_b)
    assert len(rows_a) == 3
    assert len(rows_b) == 2
    assert all(j.user_id == user_a for j in rows_a)
    assert all(j.user_id == user_b for j in rows_b)


@pytest.mark.asyncio
async def test_list_for_user_orders_desc(db_session: AsyncSession) -> None:
    """list_for_user returns jobs ordered newest-first."""
    user_id = await _create_user(db_session)
    repo = JobRepository(db_session)

    for i in range(3):
        await repo.create_job(
            user_id=user_id,
            bucket="bucket",
            prefix=f"prefix-{i}/",
            filespace_id="fs",
            datastore_id="ds",
        )
    await db_session.commit()

    rows = await repo.list_for_user(user_id)
    assert len(rows) == 3
    created_ats = [r.created_at for r in rows]
    assert created_ats == sorted(created_ats, reverse=True)


@pytest.mark.asyncio
async def test_cancel_job_belongs_to_user(db_session: AsyncSession) -> None:
    """cancel_job returns True when user_id matches the job owner."""
    user_id = await _create_user(db_session)
    repo = JobRepository(db_session)

    job = await repo.create_job(
        user_id=user_id,
        bucket="bucket",
        prefix="prefix/",
        filespace_id="fs",
        datastore_id="ds",
    )
    await db_session.commit()

    result = await repo.cancel_job(job.id, user_id)
    await db_session.commit()

    assert result is True
    updated = await repo.get(job.id)
    assert updated is not None
    assert updated.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_job_other_user_fails(db_session: AsyncSession) -> None:
    """cancel_job returns False when user_id does not match the job owner."""
    owner = await _create_user(db_session)
    other = await _create_user(db_session)
    repo = JobRepository(db_session)

    job = await repo.create_job(
        user_id=owner,
        bucket="bucket",
        prefix="prefix/",
        filespace_id="fs",
        datastore_id="ds",
    )
    await db_session.commit()

    result = await repo.cancel_job(job.id, other)
    await db_session.commit()

    assert result is False
    unchanged = await repo.get(job.id)
    assert unchanged is not None
    assert unchanged.status == "pending"


@pytest.mark.asyncio
async def test_update_status_transitions(db_session: AsyncSession) -> None:
    """update_status transitions pending → running → completed and sets timestamps."""
    user_id = await _create_user(db_session)
    repo = JobRepository(db_session)

    job = await repo.create_job(
        user_id=user_id,
        bucket="bucket",
        prefix="prefix/",
        filespace_id="fs",
        datastore_id="ds",
    )
    await db_session.commit()

    started = datetime.now(tz=timezone.utc)
    running = await repo.update_status(job.id, status="running", started_at=started)
    await db_session.commit()
    assert running is not None
    assert running.status == "running"
    assert running.started_at is not None

    completed_at = datetime.now(tz=timezone.utc)
    done = await repo.update_status(
        job.id,
        status="completed",
        completed_at=completed_at,
        completed_files=10,
        total_files=10,
    )
    await db_session.commit()
    assert done is not None
    assert done.status == "completed"
    assert done.completed_files == 10
    assert done.total_files == 10
    assert done.completed_at is not None


@pytest.mark.asyncio
async def test_update_status_returns_none_for_missing(db_session: AsyncSession) -> None:
    """update_status returns None when the job_id does not exist."""
    repo = JobRepository(db_session)
    result = await repo.update_status(999_999_999, status="completed")
    assert result is None


@pytest.mark.asyncio
async def test_timeout_stale_marks_running_jobs(db_session: AsyncSession) -> None:
    """timeout_stale fails only running jobs older than the cutoff."""
    user_id = await _create_user(db_session)
    repo = JobRepository(db_session)

    stale = await repo.create_job(
        user_id=user_id,
        bucket="bucket",
        prefix="stale/",
        filespace_id="fs",
        datastore_id="ds",
    )
    await db_session.commit()

    two_hours_ago = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    await repo.update_status(stale.id, status="running", started_at=two_hours_ago)
    await db_session.commit()

    fresh = await repo.create_job(
        user_id=user_id,
        bucket="bucket",
        prefix="fresh/",
        filespace_id="fs",
        datastore_id="ds",
    )
    await db_session.commit()
    fresh_started = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
    await repo.update_status(fresh.id, status="running", started_at=fresh_started)
    await db_session.commit()

    count = await repo.timeout_stale(hours=1)
    await db_session.commit()

    assert count == 1

    stale_row = await repo.get(stale.id)
    assert stale_row is not None
    assert stale_row.status == "failed"
    assert stale_row.error_message == "timed out"

    fresh_row = await repo.get(fresh.id)
    assert fresh_row is not None
    assert fresh_row.status == "running"


# ── delete_for_user / clear_completed_for_user ────────────────────────────────


@pytest.mark.asyncio
async def test_delete_for_user_removes_owned_job(db_session: AsyncSession) -> None:
    """delete_for_user returns True and removes the row when ownership matches."""
    owner = await _create_user(db_session)
    repo = JobRepository(db_session)

    job = await repo.create_job(
        user_id=owner,
        bucket="b",
        prefix="p/",
        filespace_id="fs",
        datastore_id="ds",
    )
    await db_session.commit()

    deleted = await repo.delete_for_user(job.id, owner)
    await db_session.commit()
    assert deleted is True
    assert await repo.get(job.id) is None


@pytest.mark.asyncio
async def test_delete_for_user_rejects_other_user(db_session: AsyncSession) -> None:
    """delete_for_user returns False and leaves the row intact for non-owners."""
    owner = await _create_user(db_session)
    other = await _create_user(db_session)
    repo = JobRepository(db_session)

    job = await repo.create_job(
        user_id=owner,
        bucket="b",
        prefix="p/",
        filespace_id="fs",
        datastore_id="ds",
    )
    await db_session.commit()

    deleted = await repo.delete_for_user(job.id, other)
    await db_session.commit()
    assert deleted is False
    survivor = await repo.get(job.id)
    assert survivor is not None
    assert survivor.user_id == owner


@pytest.mark.asyncio
async def test_clear_completed_for_user_keeps_active_jobs(
    db_session: AsyncSession,
) -> None:
    """clear_completed_for_user removes completed/failed/cancelled but keeps
    pending and running jobs."""
    user_id = await _create_user(db_session)
    repo = JobRepository(db_session)

    pending_job = await repo.create_job(
        user_id=user_id,
        bucket="b",
        prefix="pending/",
        filespace_id="fs",
        datastore_id="ds",
    )
    running_job = await repo.create_job(
        user_id=user_id,
        bucket="b",
        prefix="running/",
        filespace_id="fs",
        datastore_id="ds",
    )
    completed_job = await repo.create_job(
        user_id=user_id,
        bucket="b",
        prefix="done/",
        filespace_id="fs",
        datastore_id="ds",
    )
    failed_job = await repo.create_job(
        user_id=user_id,
        bucket="b",
        prefix="failed/",
        filespace_id="fs",
        datastore_id="ds",
    )
    cancelled_job = await repo.create_job(
        user_id=user_id,
        bucket="b",
        prefix="cancelled/",
        filespace_id="fs",
        datastore_id="ds",
    )
    await repo.update_status(running_job.id, status="running")
    await repo.update_status(completed_job.id, status="completed")
    await repo.update_status(failed_job.id, status="failed")
    await repo.update_status(cancelled_job.id, status="cancelled")
    await db_session.commit()

    removed = await repo.clear_completed_for_user(user_id)
    await db_session.commit()
    assert removed == 3

    survivors = await repo.list_for_user(user_id)
    survivor_ids = {j.id for j in survivors}
    assert survivor_ids == {pending_job.id, running_job.id}


@pytest.mark.asyncio
async def test_clear_completed_for_user_isolates_users(
    db_session: AsyncSession,
) -> None:
    """clear_completed_for_user must not touch another user's completed jobs."""
    owner = await _create_user(db_session)
    other = await _create_user(db_session)
    repo = JobRepository(db_session)

    owner_done = await repo.create_job(
        user_id=owner, bucket="b", prefix="o/", filespace_id="fs", datastore_id="ds"
    )
    other_done = await repo.create_job(
        user_id=other, bucket="b", prefix="x/", filespace_id="fs", datastore_id="ds"
    )
    await repo.update_status(owner_done.id, status="completed")
    await repo.update_status(other_done.id, status="completed")
    await db_session.commit()

    removed = await repo.clear_completed_for_user(owner)
    await db_session.commit()
    assert removed == 1
    assert await repo.get(other_done.id) is not None


# ── ProcessedJobRepository tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_processed_job_mark_processed_idempotent(
    db_session: AsyncSession,
) -> None:
    """mark_processed returns True first call, False on duplicate, one row exists."""
    repo = ProcessedJobRepository(db_session)
    job_id = f"arq:job:{uuid.uuid4()}"

    first = await repo.mark_processed(job_id, worker_id="worker-1", result_status="ok")
    await db_session.commit()
    assert first is True

    second = await repo.mark_processed(job_id, worker_id="worker-2", result_status="ok")
    await db_session.commit()
    assert second is False

    count = await repo.count()
    assert count == 1
