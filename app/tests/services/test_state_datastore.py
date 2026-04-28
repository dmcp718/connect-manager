"""Integration tests for the async datastore + SQS wrappers in services/state.py.

Verifies the full encrypt-on-write / decrypt-on-read contract using a live
Postgres 16 container (provided by the session-scoped ``postgres_container``
fixture in tests/conftest.py).
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

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-secret-32-chars-min-required-len-xxxxxxxxxxx",
)
# DATA_DIR must be set so secrets.py enters container mode and uses Fernet.
os.environ.setdefault("DATA_DIR", "/tmp/connect-test-secrets")

# Import after env vars are set so secrets module initialises correctly.
from services import state as svc  # noqa: E402


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


_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


# ── Datastore credential round-trip ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_and_get_datastore_creds_plaintext_round_trip(
    db_session: AsyncSession,
) -> None:
    """save then get returns identical plaintext access_key + secret_key."""
    user_id = await _create_user(db_session)

    await svc.save_datastore_credentials(
        db_session,
        datastore_id="ds-svc-001",
        datastore_name="TestDS",
        filespace_id="fs-svc-001",
        filespace_name="TestFS",
        bucket_name="test-bucket",
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        region="us-east-1",
        user_id=user_id,
    )
    await db_session.commit()

    result = await svc.get_datastore_credentials(db_session, "ds-svc-001", user_id)
    assert result is not None
    assert result["access_key"] == _ACCESS_KEY
    assert result["secret_key"] == _SECRET_KEY
    assert result["datastore_name"] == "TestDS"
    assert result["bucket_name"] == "test-bucket"


@pytest.mark.asyncio
async def test_datastore_creds_stored_as_ciphertext(
    db_session: AsyncSession,
) -> None:
    """The raw column value is bytes (ciphertext), not plaintext."""
    from db.repositories.datastore import DatastoreCredentialsRepository

    user_id = await _create_user(db_session)

    await svc.save_datastore_credentials(
        db_session,
        datastore_id="ds-svc-002",
        datastore_name="TestDS2",
        filespace_id="fs-svc-002",
        filespace_name="TestFS2",
        bucket_name="test-bucket-2",
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        user_id=user_id,
    )
    await db_session.commit()

    repo = DatastoreCredentialsRepository(db_session)
    row = await repo.get_for_datastore_user("ds-svc-002", user_id)
    assert row is not None
    assert isinstance(row.credentials_key, bytes)
    assert _ACCESS_KEY.encode() not in row.credentials_key
    assert _SECRET_KEY.encode() not in row.credentials_key


@pytest.mark.asyncio
async def test_delete_datastore_creds(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)

    await svc.save_datastore_credentials(
        db_session,
        datastore_id="ds-svc-del",
        datastore_name="DelDS",
        filespace_id="fs-del",
        filespace_name="FSDel",
        bucket_name="bucket-del",
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        user_id=user_id,
    )
    await db_session.commit()

    deleted = await svc.delete_datastore_credentials(db_session, "ds-svc-del", user_id)
    assert deleted is True
    await db_session.commit()

    missing = await svc.get_datastore_credentials(db_session, "ds-svc-del", user_id)
    assert missing is None


@pytest.mark.asyncio
async def test_list_all_datastore_credentials(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)

    for i in range(3):
        await svc.save_datastore_credentials(
            db_session,
            datastore_id=f"ds-lst-{i}",
            datastore_name=f"DS{i}",
            filespace_id="fs-lst",
            filespace_name="FSLST",
            bucket_name="bucket-lst",
            access_key=_ACCESS_KEY,
            secret_key=_SECRET_KEY,
            user_id=user_id,
        )
    await db_session.commit()

    rows = await svc.list_all_datastore_credentials(db_session, user_id)
    assert len(rows) == 3
    for row in rows:
        assert row["access_key"] == _ACCESS_KEY
        assert row["secret_key"] == _SECRET_KEY


# ── SQS credential round-trip ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqs_creds_round_trip(db_session: AsyncSession) -> None:
    """save_sqs_credentials then get returns identical plaintext secret_key."""
    user_id = await _create_user(db_session)

    await svc.save_sqs_credentials(
        db_session,
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        region="eu-west-1",
        user_id=user_id,
    )
    await db_session.commit()

    result = await svc.get_sqs_credentials(db_session, user_id)
    assert result is not None
    assert result["access_key"] == _ACCESS_KEY
    assert result["secret_key"] == _SECRET_KEY
    assert result["region"] == "eu-west-1"


@pytest.mark.asyncio
async def test_sqs_creds_stored_as_ciphertext(db_session: AsyncSession) -> None:
    """The raw secret_key_encrypted column is bytes (ciphertext), not plaintext."""
    from db.repositories.datastore import SqsCredentialsRepository

    user_id = await _create_user(db_session)

    await svc.save_sqs_credentials(
        db_session,
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        region="us-west-2",
        user_id=user_id,
    )
    await db_session.commit()

    repo = SqsCredentialsRepository(db_session)
    rows = await repo.list_for_user(user_id)
    assert len(rows) == 1
    assert isinstance(rows[0].secret_key_encrypted, bytes)
    assert _SECRET_KEY.encode() not in rows[0].secret_key_encrypted


@pytest.mark.asyncio
async def test_delete_sqs_creds(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)

    await svc.save_sqs_credentials(
        db_session,
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        region="ap-southeast-1",
        user_id=user_id,
    )
    await db_session.commit()

    deleted = await svc.delete_sqs_credentials(db_session, user_id)
    assert deleted is True
    await db_session.commit()

    gone = await svc.get_sqs_credentials(db_session, user_id)
    assert gone is None
