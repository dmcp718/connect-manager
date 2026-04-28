"""End-to-end happy-path test for the import flow.

Drives the FastAPI app in-process via httpx + ASGITransport from
``register`` through ``queue-an-import``. External clients
(LucidLinkClient, S3Service, the ARQ Redis pool) are mocked so the
test runs with only the testcontainers Postgres fixture — no
LucidLink filespace, no S3 bucket, no Valkey required.

The test stops once a Job row reaches status='pending' and the route
hands off to ``job_queue.add_job``. Worker pickup is covered by
``tests/services/test_worker_idempotency_integration.py``; rolling
the worker into the e2e harness would require a live ARQ + Redis,
which is out of scope here.

Original AC was kubernetes-era (kind + helm + ministack S3 +
"lucidlink-api received the import call"). Reframed for aws-fargate
posture in awsk-3r4.1.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base, DatastoreCredentials, Job, UserSetting

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_factory(
    postgres_container: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test sessionmaker pointing at the testcontainers Postgres.

    Both the FastAPI app's get_db and the JobQueue.set_sessionmaker
    pickup of an injected sessionmaker need the same factory so reads
    inside the test see writes done by the route handlers.
    """
    engine: AsyncEngine = create_async_engine(
        postgres_container,
        connect_args={"ssl": False},
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    yield factory
    # Cleanup tables touched by this test, then dispose.
    async with factory() as session:
        for tbl in (
            "import_jobs",
            "sqs_events",
            "sqs_queues",
            "sqs_credentials",
            "datastore_credentials",
            "user_settings",
            "user_sessions",
            "users",
        ):
            await session.execute(Base.metadata.tables[tbl].delete())  # type: ignore[arg-type]
        await session.commit()
    await engine.dispose()


@pytest.fixture
def mocked_externals():
    """Patch the network-bound clients main.py reaches for during the flow.

    - LucidLinkClient.list_filespaces returns one fake filespace, then
      list_datastores returns one fake datastore. The credentials modal
      bypasses S3Service.head_bucket which is mocked to return None.
    - JobQueue's redis pool is a MagicMock so add_job's enqueue path
      no-ops. The DB-write half of add_job still runs and creates the
      import_jobs row, which is what we assert on.
    """
    list_filespaces = AsyncMock(return_value=[{"id": "fs-1", "name": "test-filespace"}])
    list_datastores = AsyncMock(
        return_value=[
            {
                "id": "ds-1",
                "name": "test-datastore",
                "s3StorageParams": {"bucketName": "test-bucket"},
            }
        ]
    )
    head_bucket = AsyncMock(return_value=None)

    fake_redis = MagicMock()
    fake_redis.enqueue_job = AsyncMock(return_value=None)

    with (
        patch(
            "services.lucidlink.LucidLinkClient.list_filespaces",
            new=list_filespaces,
        ),
        patch(
            "services.lucidlink.LucidLinkClient.list_datastores",
            new=list_datastores,
        ),
        patch("services.s3_service.S3Service.head_bucket", new=head_bucket),
    ):
        yield {
            "list_filespaces": list_filespaces,
            "list_datastores": list_datastores,
            "head_bucket": head_bucket,
            "fake_redis": fake_redis,
        }


@pytest_asyncio.fixture
async def app_client(
    db_factory: async_sessionmaker[AsyncSession],
    mocked_externals: dict[str, Any],
) -> AsyncIterator[httpx.AsyncClient]:
    """Build the prod FastAPI app, override get_db, wire the JobQueue
    against the test session, and return an httpx client that follows
    cookies between requests."""
    # main.app imports services.activity_logger — wire its persistence
    # against the same test session so the fire-and-forget DB inserts
    # don't go to a stale sessionmaker.
    from main import app
    from services.activity_logger import configure_persistence
    from services.database import get_db
    from services.job_queue import job_queue

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    configure_persistence(db_factory)
    job_queue.set_sessionmaker(db_factory)
    job_queue._redis_pool = mocked_externals["fake_redis"]  # type: ignore[attr-defined]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.pop(get_db, None)
    job_queue._redis_pool = None  # type: ignore[attr-defined]


# ── Test ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_import_flow(
    app_client: httpx.AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    mocked_externals: dict[str, Any],
) -> None:
    """register → login (via cookie set on register) → load_filespaces →
    save DataStore creds → queue an import → assert DB state."""
    # 1. Register
    reg = await app_client.post(
        "/api/auth/register",
        data={
            "email": f"e2e-{uuid.uuid4()}@example.com",
            "password": "E2EPass1!",
            "display_name": "E2E User",
        },
    )
    assert reg.status_code == 200, reg.text
    body = reg.json()
    user_id = body["user_id"]
    token = reg.cookies.get("access_token")
    assert token, "register did not set access_token cookie"

    # All subsequent requests carry the cookie via the client jar.
    auth_cookies = {"access_token": token}

    # 2. Load filespaces (mocked LucidLinkClient).
    lf = await app_client.post(
        "/api/load-filespaces",
        data={
            "token": "fake-bearer-token",
            "api_host": "https://test.lucidlink.example/api/v1",
        },
        cookies=auth_cookies,
    )
    assert lf.status_code == 200, lf.text
    assert mocked_externals["list_filespaces"].await_count == 1
    # Mocked list_datastores fires once for the auto-load step.
    assert mocked_externals["list_datastores"].await_count == 1

    # api_host should now be in user_settings.
    async with db_factory() as session:
        result = await session.execute(
            select(UserSetting.value).where(
                UserSetting.user_id == uuid.UUID(user_id),
                UserSetting.key == "api_host",
            )
        )
        saved_host = result.scalar_one_or_none()
        assert saved_host == "https://test.lucidlink.example/api/v1"

    # 3. Save DataStore credentials (mocked S3 head_bucket).
    save = await app_client.post(
        "/api/datastores/ds-1/credentials",
        data={
            "datastore_name": "test-datastore",
            "filespace_id": "fs-1",
            "filespace_name": "test-filespace",
            "bucket_name": "test-bucket",
            "access_key": "AKIAEXAMPLE",
            "secret_key": "secretvalueAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "region": "us-east-1",
        },
        cookies=auth_cookies,
    )
    assert save.status_code == 200, save.text
    assert mocked_externals["head_bucket"].await_count == 1

    # DataStoreCredentials row written, encrypted at rest.
    async with db_factory() as session:
        result = await session.execute(
            select(DatastoreCredentials).where(
                DatastoreCredentials.datastore_id == "ds-1",
                DatastoreCredentials.user_id == uuid.UUID(user_id),
            )
        )
        cred = result.scalar_one_or_none()
        assert cred is not None
        assert cred.bucket_name == "test-bucket"
        assert cred.filespace_id == "fs-1"
        # credentials_key holds Fernet ciphertext (bytes), not plaintext.
        assert isinstance(cred.credentials_key, (bytes, bytearray))
        assert len(cred.credentials_key) > 0

    # 4. Queue an import.
    queue = await app_client.post(
        "/api/import/folder",
        data={"datastore_id": "ds-1", "prefix": "my/folder/"},
        cookies=auth_cookies,
    )
    assert queue.status_code == 200, queue.text

    # 5. Assert: import_jobs row created with status='pending'; ARQ enqueue
    #    fired against the mocked Redis pool.
    async with db_factory() as session:
        result = await session.execute(
            select(Job).where(Job.user_id == uuid.UUID(user_id))
        )
        jobs = result.scalars().all()
        assert len(jobs) == 1
        assert jobs[0].bucket == "test-bucket"
        assert jobs[0].prefix == "my/folder/"
        assert jobs[0].filespace_id == "fs-1"
        assert jobs[0].datastore_id == "ds-1"
        assert jobs[0].status == "pending"

    fake_redis = mocked_externals["fake_redis"]
    assert fake_redis.enqueue_job.await_count == 1
    fake_redis.enqueue_job.assert_awaited_with("import_job", jobs[0].id)
