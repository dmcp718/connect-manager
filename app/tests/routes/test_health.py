"""Tests for the /health, /ready, and /metrics endpoints.

Each test builds its own minimal FastAPI app to avoid pulling in app/main.py.
"""

from __future__ import annotations

import os

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip(
        "DATABASE_URL not set — local Postgres required", allow_module_level=True
    )

import httpx
from fastapi import FastAPI
from routes.health import router

_app = FastAPI()
_app.include_router(router)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness_always_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness probe must return 200 even when DATABASE_URL points nowhere."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://nope:nope@127.0.0.1:1/x")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_200_when_all_healthy() -> None:
    """All checks pass against the local dev stack."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_503_postgres_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Postgres check fails when DATABASE_URL points at a closed port."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://connect:connect@127.0.0.1:19999/connect"
    )
    monkeypatch.setenv("DATABASE_URL_DISABLE_SSL", "1")

    # Reset the engine singleton so the monkeypatched URL is picked up.
    import services.database as db_mod

    original_engine = db_mod._engine
    db_mod._engine = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app), base_url="http://test"
        ) as client:
            response = await client.get("/ready")
    finally:
        db_mod._engine = original_engine

    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["postgres"] != "ok"
    assert checks["valkey"] == "ok"
    assert checks["aws"] == "ok"


@pytest.mark.asyncio
async def test_ready_503_valkey_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valkey check fails when VALKEY_PORT points at a closed port."""
    monkeypatch.setenv("VALKEY_PORT", "19998")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["valkey"] != "ok"


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_content_type_and_names() -> None:
    """Metrics endpoint returns Prometheus text format with all six metric names."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app), base_url="http://test"
    ) as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    for name in (
        "connect_http_requests_total",
        "connect_http_request_duration_seconds",
        "connect_arq_jobs_total",
        "connect_arq_job_duration_seconds",
        "connect_arq_queue_depth",
        "connect_db_pool_in_use",
    ):
        assert name in body, f"metric {name!r} missing from /metrics output"
