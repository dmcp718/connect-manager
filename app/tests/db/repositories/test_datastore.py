"""Integration tests for DatastoreCredentialsRepository, SqsCredentialsRepository,
and SqsQueueRepository against a live Postgres 16 container.

The shared ``postgres_container`` fixture (session-scoped) runs Alembic
``upgrade head`` once before any test in the session runs.
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
from db.repositories.datastore import (
    DatastoreCredentialsRepository,
    SqsCredentialsRepository,
    SqsQueueRepository,
)

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
            "sqs_events",
            "sqs_queues",
            "sqs_credentials",
            "datastore_credentials",
            "user_sessions",
            "users",
        ):
            await session.execute(Base.metadata.tables[tbl].delete())  # type: ignore[arg-type]
        await session.commit()
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


_FAKE_CIPHERTEXT_A = b"FAKE_CIPHERTEXT_AAAAAAAAAAAAAAAAAA"
_FAKE_CIPHERTEXT_B = b"FAKE_CIPHERTEXT_BBBBBBBBBBBBBBBBBB"


# ── DatastoreCredentialsRepository ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_and_get_datastore_creds(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = DatastoreCredentialsRepository(db_session)

    saved = await repo.upsert(
        datastore_id="ds-001",
        datastore_name="MyDS",
        filespace_id="fs-001",
        filespace_name="MyFS",
        bucket_name="my-bucket",
        credentials_key=_FAKE_CIPHERTEXT_A,
        region="us-east-1",
        user_id=user_id,
    )
    await db_session.commit()

    fetched = await repo.get_for_datastore_user("ds-001", user_id)
    assert fetched is not None
    assert fetched.id == saved.id
    assert fetched.datastore_name == "MyDS"
    assert fetched.bucket_name == "my-bucket"
    assert fetched.credentials_key == _FAKE_CIPHERTEXT_A


@pytest.mark.asyncio
async def test_upsert_replaces_existing(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = DatastoreCredentialsRepository(db_session)

    await repo.upsert(
        datastore_id="ds-dup",
        datastore_name="OriginalName",
        filespace_id="fs-x",
        filespace_name="FSX",
        bucket_name="bucket-x",
        credentials_key=_FAKE_CIPHERTEXT_A,
        user_id=user_id,
    )
    await db_session.commit()

    await repo.upsert(
        datastore_id="ds-dup",
        datastore_name="UpdatedName",
        filespace_id="fs-x",
        filespace_name="FSX",
        bucket_name="bucket-x",
        credentials_key=_FAKE_CIPHERTEXT_B,
        user_id=user_id,
    )
    await db_session.commit()

    rows = await repo.list_for_user(user_id)
    assert len(rows) == 1
    assert rows[0].datastore_name == "UpdatedName"
    assert rows[0].credentials_key == _FAKE_CIPHERTEXT_B


@pytest.mark.asyncio
async def test_list_for_user(db_session: AsyncSession) -> None:
    user_a = await _create_user(db_session)
    user_b = await _create_user(db_session)
    repo = DatastoreCredentialsRepository(db_session)

    for i in range(3):
        await repo.upsert(
            datastore_id=f"ds-a-{i}",
            datastore_name=f"DSA{i}",
            filespace_id="fs-a",
            filespace_name="FSA",
            bucket_name="bucket-a",
            credentials_key=_FAKE_CIPHERTEXT_A,
            user_id=user_a,
        )
    for i in range(2):
        await repo.upsert(
            datastore_id=f"ds-b-{i}",
            datastore_name=f"DSB{i}",
            filespace_id="fs-b",
            filespace_name="FSB",
            bucket_name="bucket-b",
            credentials_key=_FAKE_CIPHERTEXT_B,
            user_id=user_b,
        )
    await db_session.commit()

    rows_a = await repo.list_for_user(user_a)
    rows_b = await repo.list_for_user(user_b)
    assert len(rows_a) == 3
    assert len(rows_b) == 2


@pytest.mark.asyncio
async def test_delete_returns_true_when_present_false_when_not(
    db_session: AsyncSession,
) -> None:
    user_id = await _create_user(db_session)
    repo = DatastoreCredentialsRepository(db_session)

    await repo.upsert(
        datastore_id="ds-del",
        datastore_name="ToDelete",
        filespace_id="fs-d",
        filespace_name="FSD",
        bucket_name="bucket-d",
        credentials_key=_FAKE_CIPHERTEXT_A,
        user_id=user_id,
    )
    await db_session.commit()

    deleted = await repo.delete_for_datastore_user("ds-del", user_id)
    assert deleted is True
    await db_session.commit()

    missing = await repo.delete_for_datastore_user("ds-del", user_id)
    assert missing is False


# ── SqsCredentialsRepository ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqs_creds_round_trip(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = SqsCredentialsRepository(db_session)

    created = await repo.create(
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key_encrypted=_FAKE_CIPHERTEXT_A,
        region="eu-west-1",
        user_id=user_id,
    )
    await db_session.commit()

    rows = await repo.list_for_user(user_id)
    assert len(rows) == 1
    assert rows[0].id == created.id
    assert rows[0].access_key == "AKIAIOSFODNN7EXAMPLE"
    assert rows[0].secret_key_encrypted == _FAKE_CIPHERTEXT_A
    assert rows[0].region == "eu-west-1"

    deleted = await repo.delete_for_user(user_id)
    assert deleted == 1
    await db_session.commit()

    empty = await repo.list_for_user(user_id)
    assert len(empty) == 0


# ── SqsQueueRepository ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqs_queue_create_and_list_for_user(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = SqsQueueRepository(db_session)

    q1 = await repo.create(
        queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/q1",
        name="q1",
        region="us-east-1",
        datastore_id="ds-001",
        filespace_id="fs-001",
        import_prefix="",
        user_id=user_id,
    )
    q2 = await repo.create(
        queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/q2",
        name="q2",
        region="us-east-1",
        datastore_id="ds-001",
        filespace_id="fs-001",
        import_prefix="prefix/",
        user_id=user_id,
    )
    await db_session.commit()

    queues = await repo.list_for_user(user_id)
    assert len(queues) == 2
    ids = {q.id for q in queues}
    assert q1.id in ids
    assert q2.id in ids


@pytest.mark.asyncio
async def test_sqs_queue_mark_polled_clears_error_message(
    db_session: AsyncSession,
) -> None:
    user_id = await _create_user(db_session)
    repo = SqsQueueRepository(db_session)

    queue = await repo.create(
        queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/qclr",
        name="qclr",
        region="us-east-1",
        datastore_id="ds-002",
        filespace_id="fs-002",
        import_prefix="",
        user_id=user_id,
    )
    await db_session.commit()

    await repo.mark_polled(queue.id, error_message="some error")
    await db_session.commit()

    row = await repo.get(queue.id)
    assert row is not None
    assert row.error_message == "some error"
    assert row.last_poll_at is not None

    await repo.mark_polled(queue.id, error_message=None)
    await db_session.commit()

    row2 = await repo.get(queue.id)
    assert row2 is not None
    assert row2.error_message is None


@pytest.mark.asyncio
async def test_sqs_queue_mark_polled_with_error_sets_message(
    db_session: AsyncSession,
) -> None:
    user_id = await _create_user(db_session)
    repo = SqsQueueRepository(db_session)

    queue = await repo.create(
        queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/qerr",
        name="qerr",
        region="us-east-1",
        datastore_id="ds-003",
        filespace_id="fs-003",
        import_prefix="",
        user_id=user_id,
    )
    await db_session.commit()

    ok = await repo.mark_polled(queue.id, error_message="connection refused")
    assert ok is True
    await db_session.commit()

    row = await repo.get(queue.id)
    assert row is not None
    assert row.error_message == "connection refused"
    assert row.last_poll_at is not None
