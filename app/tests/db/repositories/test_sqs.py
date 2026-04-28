"""Integration tests for SqsEventRepository, SqsQueueRepository, and the
new user-scoped methods on SqsCredentialsRepository.

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

from db.models import Base, SqsQueue, User
from db.repositories.datastore import (
    SqsCredentialsRepository,
    SqsEventRepository,
    SqsQueueRepository,
)

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


@pytest_asyncio.fixture
async def db_session(postgres_container: str) -> AsyncIterator[AsyncSession]:
    """Fresh AsyncSession per test; truncates SQS tables after each test."""
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
            "sqs_events",
            "sqs_queues",
            "sqs_credentials",
            "user_sessions",
            "users",
        ):
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


async def _create_queue(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    name: str = "queue",
    status: str = "active",
) -> SqsQueue:
    repo = SqsQueueRepository(session)
    return await repo.create(
        queue_url=f"https://sqs.us-east-1.amazonaws.com/123/{name}",
        queue_arn=f"arn:aws:sqs:us-east-1:123:{name}",
        name=name,
        region="us-east-1",
        datastore_id="ds-1",
        filespace_id="fs-1",
        import_prefix="",
        status=status,
        user_id=user_id,
    )


# ── SqsCredentialsRepository ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqs_creds_get_for_user_returns_first_row(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    repo = SqsCredentialsRepository(db_session)
    await repo.upsert_for_user(
        access_key="AKIAEX",
        secret_key_encrypted=b"\x80abcd",
        region="us-east-1",
        user_id=user_id,
    )
    await db_session.commit()

    row = await repo.get_for_user(user_id)
    assert row is not None
    assert row.access_key == "AKIAEX"
    assert row.region == "us-east-1"


@pytest.mark.asyncio
async def test_sqs_creds_upsert_replaces_existing(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    repo = SqsCredentialsRepository(db_session)

    await repo.upsert_for_user("AKIA1", b"\x80one", "us-east-1", user_id)
    await db_session.commit()
    await repo.upsert_for_user("AKIA2", b"\x80two", "us-west-2", user_id)
    await db_session.commit()

    rows = await repo.list_for_user(user_id)
    assert len(rows) == 1
    assert rows[0].access_key == "AKIA2"
    assert rows[0].region == "us-west-2"


# ── SqsQueueRepository ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqs_queue_list_active_returns_only_active(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    await _create_queue(db_session, user_id, name="active-1", status="active")
    await _create_queue(db_session, user_id, name="paused-1", status="paused")
    await _create_queue(db_session, user_id, name="active-2", status="active")
    await db_session.commit()

    repo = SqsQueueRepository(db_session)
    rows = list(await repo.list_active())
    names = {q.name for q in rows}
    assert "active-1" in names
    assert "active-2" in names
    assert "paused-1" not in names


@pytest.mark.asyncio
async def test_sqs_queue_get_for_user_isolates_users(db_session: AsyncSession):
    owner = await _create_user(db_session)
    other = await _create_user(db_session)
    queue = await _create_queue(db_session, owner)
    await db_session.commit()

    repo = SqsQueueRepository(db_session)
    assert await repo.get_for_user(queue.id, owner) is not None
    assert await repo.get_for_user(queue.id, other) is None


@pytest.mark.asyncio
async def test_sqs_queue_update_status_for_user(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    queue = await _create_queue(db_session, user_id, status="active")
    await db_session.commit()

    repo = SqsQueueRepository(db_session)
    ok = await repo.update_status_for_user(
        queue.id, user_id, status="paused", error_message=""
    )
    await db_session.commit()
    assert ok is True

    refreshed = await repo.get_for_user(queue.id, user_id)
    assert refreshed is not None
    assert refreshed.status == "paused"


@pytest.mark.asyncio
async def test_sqs_queue_delete_for_user_cascades_events(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    queue = await _create_queue(db_session, user_id)
    events = SqsEventRepository(db_session)
    await events.create_event(
        queue_id=queue.id,
        message_id="m-1",
        event_type="ObjectCreated:Put",
        bucket="b",
        object_key="k",
    )
    await db_session.commit()

    queue_repo = SqsQueueRepository(db_session)
    deleted = await queue_repo.delete_for_user(queue.id, user_id)
    await db_session.commit()
    assert deleted is True

    # ON DELETE CASCADE wipes the child rows.
    remaining = await events.list_for_user(user_id)
    assert remaining == []


# ── SqsEventRepository ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqs_event_create_then_get(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    queue = await _create_queue(db_session, user_id)
    await db_session.commit()

    repo = SqsEventRepository(db_session)
    event = await repo.create_event(
        queue_id=queue.id,
        message_id="m-1",
        event_type="ObjectCreated:Put",
        bucket="b",
        object_key="k",
    )
    await db_session.commit()
    assert event.status == "pending"
    fetched = await repo.get(event.id)
    assert fetched is not None
    assert fetched.message_id == "m-1"


@pytest.mark.asyncio
async def test_sqs_event_exists_for_message_idempotency(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    queue = await _create_queue(db_session, user_id)
    repo = SqsEventRepository(db_session)
    await repo.create_event(
        queue_id=queue.id,
        message_id="m-1",
        event_type="ObjectCreated:Put",
        bucket="b",
        object_key="k",
    )
    await db_session.commit()

    assert await repo.exists_for_message("m-1", queue.id, "k") is True
    assert await repo.exists_for_message("m-1", queue.id, "different-key") is False
    assert await repo.exists_for_message("m-other", queue.id, "k") is False


@pytest.mark.asyncio
async def test_sqs_event_update_status(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    queue = await _create_queue(db_session, user_id)
    repo = SqsEventRepository(db_session)
    event = await repo.create_event(
        queue_id=queue.id,
        message_id="m",
        event_type="ObjectCreated:Put",
        bucket="b",
        object_key="k",
    )
    await db_session.commit()

    ok = await repo.update_status(event.id, status="completed", job_id="job-1")
    await db_session.commit()
    assert ok is True
    refreshed = await repo.get(event.id)
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.job_id == "job-1"


@pytest.mark.asyncio
async def test_sqs_event_list_for_user_eager_loads_queue(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    queue = await _create_queue(db_session, user_id, name="my-queue")
    repo = SqsEventRepository(db_session)
    await repo.create_event(
        queue_id=queue.id,
        message_id="m",
        event_type="ObjectCreated:Put",
        bucket="b",
        object_key="k",
    )
    await db_session.commit()

    rows = list(await repo.list_for_user(user_id, limit=10))
    assert len(rows) == 1
    # Queue eager-loaded by selectinload — accessing .queue.name does not
    # trigger another query (verified implicitly: no IO here).
    assert rows[0].queue.name == "my-queue"


@pytest.mark.asyncio
async def test_sqs_event_count_today_for_queue(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    queue = await _create_queue(db_session, user_id)
    repo = SqsEventRepository(db_session)
    for i in range(3):
        await repo.create_event(
            queue_id=queue.id,
            message_id=f"m-{i}",
            event_type="ObjectCreated:Put",
            bucket="b",
            object_key=f"k-{i}",
        )
    await db_session.commit()

    count = await repo.count_today_for_queue(queue.id, user_id)
    assert count == 3


@pytest.mark.asyncio
async def test_sqs_event_clear_for_user_isolates_users(db_session: AsyncSession):
    owner = await _create_user(db_session)
    other = await _create_user(db_session)
    owner_queue = await _create_queue(db_session, owner, name="o-q")
    other_queue = await _create_queue(db_session, other, name="x-q")
    repo = SqsEventRepository(db_session)
    await repo.create_event(
        queue_id=owner_queue.id,
        message_id="m1",
        event_type="x",
        bucket="b",
        object_key="k1",
    )
    await repo.create_event(
        queue_id=other_queue.id,
        message_id="m2",
        event_type="x",
        bucket="b",
        object_key="k2",
    )
    await db_session.commit()

    deleted = await repo.clear_for_user(owner)
    await db_session.commit()
    assert deleted == 1

    assert len(await repo.list_for_user(owner)) == 0
    assert len(await repo.list_for_user(other)) == 1


@pytest.mark.asyncio
async def test_sqs_event_clear_old_drops_aged_rows(db_session: AsyncSession):
    user_id = await _create_user(db_session)
    queue = await _create_queue(db_session, user_id)
    repo = SqsEventRepository(db_session)
    fresh = await repo.create_event(
        queue_id=queue.id,
        message_id="fresh",
        event_type="x",
        bucket="b",
        object_key="k1",
    )
    aged = await repo.create_event(
        queue_id=queue.id,
        message_id="aged",
        event_type="x",
        bucket="b",
        object_key="k2",
    )
    # Backdate the second event so it falls outside the 7-day window.
    aged.created_at = datetime.now(tz=timezone.utc) - timedelta(days=14)
    await db_session.commit()

    deleted = await repo.clear_old(days=7)
    await db_session.commit()
    assert deleted == 1

    assert await repo.get(fresh.id) is not None
    assert await repo.get(aged.id) is None
