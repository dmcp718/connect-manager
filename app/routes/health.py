"""K8s probe and Prometheus metrics endpoints.

/health   — liveness: unconditional 200 if the process is alive
/ready    — readiness: checks Postgres, Valkey, and AWS credentials
/metrics  — Prometheus text exposition; metrics populated by Epic 4
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import boto3  # type: ignore[import-untyped]
import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import text

import prometheus_client
from services.database import get_engine
from services.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["health"])

# ---------------------------------------------------------------------------
# Draining flag — set by the SIGTERM handler in services.shutdown so that
# /ready returns 503 immediately while the load balancer finishes draining.
# /health is intentionally NOT affected: the process is still alive.
# ---------------------------------------------------------------------------

_shutting_down: bool = False


def set_draining() -> None:
    global _shutting_down
    _shutting_down = True


def is_draining() -> bool:
    return _shutting_down


# ---------------------------------------------------------------------------
# Prometheus metric registry — registered once at import time.
# Populated with values by Epic 4 instrumentation; declared here so /metrics
# always returns a stable set of metric names.
# ---------------------------------------------------------------------------

http_requests_total = prometheus_client.Counter(
    "connect_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

http_request_duration_seconds = prometheus_client.Histogram(
    "connect_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
)

arq_jobs_total = prometheus_client.Counter(
    "connect_arq_jobs_total",
    "Total ARQ jobs processed",
    ["job_type", "outcome"],
)

arq_job_duration_seconds = prometheus_client.Histogram(
    "connect_arq_job_duration_seconds",
    "ARQ job duration",
    ["job_type"],
)

arq_queue_depth = prometheus_client.Gauge(
    "connect_arq_queue_depth",
    "ARQ queue depth",
    ["queue"],
)

# SPEC §8.1: connect_db_pool_in_use is a label-less gauge.
db_pool_in_use = prometheus_client.Gauge(
    "connect_db_pool_in_use",
    "Database connection pool connections in use",
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness() -> Response:
    if is_draining():
        return Response(
            content=json.dumps({"status": "draining"}),
            status_code=503,
            media_type="application/json",
        )

    checks: dict[str, str] = {}

    # Postgres
    try:
        engine = get_engine()

        async def _pg_check() -> None:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        await asyncio.wait_for(_pg_check(), timeout=2.0)
        checks["postgres"] = "ok"
    except Exception as exc:
        msg = str(exc)
        log.warning("readiness check failed", extra={"check": "postgres", "error": msg})
        checks["postgres"] = msg

    # Valkey
    from services.valkey import make_redis_client

    client: "redis.Redis | None" = None
    try:
        client = make_redis_client(socket_timeout=2.0)
        await asyncio.wait_for(client.ping(), timeout=2.0)
        checks["valkey"] = "ok"
    except Exception as exc:
        msg = str(exc)
        log.warning("readiness check failed", extra={"check": "valkey", "error": msg})
        checks["valkey"] = msg
    finally:
        if client is not None:
            await client.aclose()

    # AWS credential resolution — no network call; just inspect the cred chain.
    try:
        creds = boto3.Session().get_credentials()
        if creds is None:
            raise RuntimeError("no credentials resolved")
        checks["aws"] = "ok"
    except Exception as exc:
        msg = str(exc)
        log.warning("readiness check failed", extra={"check": "aws", "error": msg})
        checks["aws"] = msg

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503
    body: dict[str, Any] = (
        {"status": "ok"} if all_ok else {"status": "not_ready", "checks": checks}
    )

    return Response(
        content=json.dumps(body),
        status_code=status_code,
        media_type="application/json",
    )


@router.get("/metrics")
async def metrics() -> PlainTextResponse:
    # generate_latest() emits the Prometheus 0.0.4 text format; pin the
    # content-type accordingly so scrapers (CloudWatch Container Insights,
    # kube-prometheus-stack) parse it without OpenMetrics negotiation.
    data = prometheus_client.generate_latest()
    return PlainTextResponse(
        content=data.decode("utf-8") if isinstance(data, bytes) else data,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
