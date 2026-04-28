"""Shared pytest fixtures for the Connect Manager test suite."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import requests
from fakeredis import FakeAsyncRedis
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[str]:
    """Start a PostgreSQL 16 container and run Alembic migrations.

    Yields the asyncpg DSN (``postgresql+asyncpg://...``).
    Container is shared across the entire test session.
    """
    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        sync_url = pg.get_connection_url()
        asyncpg_url = sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)

        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        env = {**os.environ, "DATABASE_URL": sync_url.replace("postgresql://", "postgresql+psycopg://", 1)}
        subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=repo_root,
            env=env,
            check=True,
            capture_output=True,
        )

        yield asyncpg_url


@pytest_asyncio.fixture
async def valkey_fake() -> AsyncIterator[FakeAsyncRedis]:
    """Provide an in-process FakeAsyncRedis instance, reset between tests."""
    client: FakeAsyncRedis = FakeAsyncRedis()
    yield client
    await client.aclose()


@pytest.fixture(scope="session")
def valkey_container() -> Iterator[str]:
    """Start a real Valkey 8 container.

    Yields the connection URL.  Opt-in: only tests that declare this fixture
    incur the container startup cost.
    """
    with RedisContainer("valkey:8-alpine") as valkey:
        host = valkey.get_container_host_ip()
        port = valkey.get_exposed_port(6379)
        yield f"redis://{host}:{port}"


@pytest.fixture(scope="session")
def ministack_endpoint() -> str:
    """Return the ministack base URL, or skip if it is not reachable."""
    url = "http://localhost:4566"
    try:
        resp = requests.get(f"{url}/_localstack/health", timeout=1.0)
        resp.raise_for_status()
    except Exception:
        pytest.skip("ministack not reachable")
    return url
