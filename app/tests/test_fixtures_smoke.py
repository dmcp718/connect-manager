"""Smoke tests that verify conftest fixtures work correctly."""

from __future__ import annotations

import pytest
import psycopg
from fakeredis import FakeAsyncRedis


def test_postgres_container_has_schema(postgres_container: str) -> None:
    """Confirm alembic ran and the users table exists in the container DB."""
    sync_url = postgres_container.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(sync_url) as conn:
        result = conn.execute("SELECT to_regclass('public.users')").fetchone()
    assert result is not None and result[0] is not None


async def test_valkey_fake_pingable(valkey_fake: FakeAsyncRedis) -> None:
    """Confirm FakeAsyncRedis responds to PING."""
    assert await valkey_fake.ping()


def test_ministack_endpoint(ministack_endpoint: str) -> None:
    """Confirm ministack fixture returns the correct URL."""
    assert ministack_endpoint == "http://localhost:4566"


@pytest.mark.real_aws
def test_excluded_by_default() -> None:
    """This test must NEVER run under default addopts (real_aws marker)."""
    assert False, "real_aws test should be deselected by addopts"
