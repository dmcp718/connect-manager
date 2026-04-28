"""Tests for Prometheus metrics instrumentation (awsk-tdq.9, SPEC §8.1).

Covers:

* HTTP middleware increments ``connect_http_requests_total`` and observes
  ``connect_http_request_duration_seconds`` with the route's path template.
* ARQ wrapper increments ``connect_arq_jobs_total{job_type, outcome}`` for
  both success and failure, and observes ``connect_arq_job_duration_seconds``.
* Background sampler updates ``connect_arq_queue_depth`` and
  ``connect_db_pool_in_use`` from a Valkey-like client and a stub pool.
* ``/metrics`` returns Prometheus text exposition with the expected
  histogram suffix lines (``_bucket``, ``_count``, ``_sum``).
* All six metric names declared in SPEC §8.1 use the documented label set
  exactly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI

import prometheus_client
from middleware.metrics import PrometheusMiddleware
from routes.health import (
    arq_job_duration_seconds,
    arq_jobs_total,
    arq_queue_depth,
    db_pool_in_use,
    http_request_duration_seconds,
    http_requests_total,
    router as health_router,
)
from services.metrics import (
    _sample_once,
    instrumented,
    start_metrics_sampler,
    stop_metrics_sampler,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_test_app() -> FastAPI:
    """A minimal app with the metrics middleware and a couple of routes."""
    app = FastAPI()
    app.add_middleware(PrometheusMiddleware)
    app.include_router(health_router)

    @app.get("/api/datastores/{datastore_id}")
    async def _datastore(datastore_id: str) -> dict[str, str]:
        return {"id": datastore_id}

    @app.get("/api/ping")
    async def _ping() -> dict[str, str]:
        return {"pong": "yes"}

    return app


def _counter_value(counter: Any, **labels: str) -> float:
    """Read a labelled counter's current ``_total`` value."""
    return counter.labels(**labels)._value.get()  # type: ignore[no-any-return]


def _histogram_count(hist: Any, **labels: str) -> int:
    """Read the ``_count`` (number of observations) for a labelled histogram.

    Uses ``hist.collect()`` and matches on the labelset; ``_count`` is exposed
    as a ``_count``-suffixed sample on the histogram metric family.
    """
    for family in hist.collect():
        for sample in family.samples:
            if sample.name.endswith("_count") and sample.labels == labels:
                return int(sample.value)
    return 0


# ── HTTP middleware ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_counter_increments_per_request() -> None:
    app = _build_test_app()
    before = _counter_value(
        http_requests_total, method="GET", path="/api/ping", status="200"
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(3):
            r = await client.get("/api/ping")
            assert r.status_code == 200

    after = _counter_value(
        http_requests_total, method="GET", path="/api/ping", status="200"
    )
    assert after - before == 3


@pytest.mark.asyncio
async def test_http_path_label_uses_route_template() -> None:
    """The {datastore_id} placeholder must NOT be replaced with the rendered value."""
    app = _build_test_app()
    template = "/api/datastores/{datastore_id}"
    before = _counter_value(
        http_requests_total, method="GET", path=template, status="200"
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.get("/api/datastores/abc-123")
        r2 = await client.get("/api/datastores/zzz-999")
        assert r1.status_code == 200
        assert r2.status_code == 200

    after = _counter_value(
        http_requests_total, method="GET", path=template, status="200"
    )
    assert after - before == 2

    # And the histogram observed both requests under the same template label.
    count = _histogram_count(http_request_duration_seconds, method="GET", path=template)
    assert count >= 2


# ── ARQ wrapper ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_instrumented_records_success() -> None:
    @instrumented
    async def example_job(ctx: dict[str, Any], n: int) -> int:
        return n + 1

    before = _counter_value(arq_jobs_total, job_type="example_job", outcome="success")
    result = await example_job({}, 41)
    after = _counter_value(arq_jobs_total, job_type="example_job", outcome="success")

    assert result == 42
    assert after - before == 1
    assert _histogram_count(arq_job_duration_seconds, job_type="example_job") >= 1


@pytest.mark.asyncio
async def test_instrumented_records_failure() -> None:
    @instrumented
    async def boom_job(ctx: dict[str, Any]) -> None:
        raise RuntimeError("kaboom")

    before = _counter_value(arq_jobs_total, job_type="boom_job", outcome="failure")
    with pytest.raises(RuntimeError, match="kaboom"):
        await boom_job({})
    after = _counter_value(arq_jobs_total, job_type="boom_job", outcome="failure")

    assert after - before == 1


# ── Background sampler ────────────────────────────────────────────────────────


class _StubPool:
    def __init__(self, n: int) -> None:
        self._n = n

    def checkedout(self) -> int:
        return self._n


class _StubEngine:
    def __init__(self, n: int) -> None:
        self.pool = _StubPool(n)


@pytest.mark.asyncio
async def test_sample_once_updates_queue_and_pool_gauges() -> None:
    fake = FakeAsyncRedis()
    queue = "arq:queue:metric-test"
    # Push three items so llen reports 3.
    await fake.rpush(queue, "a", "b", "c")  # type: ignore[misc]

    engine = _StubEngine(7)

    await _sample_once(fake, engine, queue)  # type: ignore[arg-type]

    assert arq_queue_depth.labels(queue=queue)._value.get() == 3
    assert db_pool_in_use._value.get() == 7

    await fake.aclose()


@pytest.mark.asyncio
async def test_sampler_lifecycle_starts_and_stops() -> None:
    """start_metrics_sampler returns a task that stop_metrics_sampler cancels."""
    task = start_metrics_sampler(
        valkey_host="127.0.0.1",
        valkey_port=1,  # closed port → sampler degrades silently
        queue_name="arq:queue:lifecycle-test",
        interval=0.05,
    )
    assert task is not None
    # Give the loop a tick.
    await asyncio.sleep(0.1)
    await stop_metrics_sampler()
    assert task.cancelled() or task.done()


# ── /metrics exposition ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_histogram_lines() -> None:
    app = _build_test_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Generate at least one HTTP histogram observation.
        await client.get("/api/ping")

        # And one ARQ job histogram observation.
        @instrumented
        async def warmup(ctx: dict[str, Any]) -> None:
            return None

        await warmup({})

        response = await client.get("/metrics")

    assert response.status_code == 200
    ct = response.headers["content-type"]
    assert ct.startswith("text/plain")
    assert "version=0.0.4" in ct

    body = response.text
    # Histogram exposition lines must include _bucket, _count, _sum suffixes.
    for prefix in (
        "connect_http_request_duration_seconds",
        "connect_arq_job_duration_seconds",
    ):
        assert f"{prefix}_bucket" in body, f"{prefix}_bucket missing"
        assert f"{prefix}_count" in body, f"{prefix}_count missing"
        assert f"{prefix}_sum" in body, f"{prefix}_sum missing"


# ── SPEC §8.1 label-shape contract ────────────────────────────────────────────


def _label_names(metric: Any) -> tuple[str, ...]:
    return tuple(metric._labelnames)


def test_metric_label_names_match_spec() -> None:
    """SPEC §8.1 row-by-row label-set check."""
    assert _label_names(http_requests_total) == ("method", "path", "status")
    assert _label_names(http_request_duration_seconds) == ("method", "path")
    assert _label_names(arq_jobs_total) == ("job_type", "outcome")
    assert _label_names(arq_job_duration_seconds) == ("job_type",)
    assert _label_names(arq_queue_depth) == ("queue",)
    # connect_db_pool_in_use is label-less per SPEC §8.1.
    assert _label_names(db_pool_in_use) == ()


def test_all_six_metric_names_registered() -> None:
    """Every metric named in SPEC §8.1 must be findable in the global registry.

    Note: prometheus_client strips the ``_total`` suffix from counter names
    in ``MetricFamily.name`` (the suffix is restored on the wire). We map
    the expected SPEC-level names accordingly.
    """
    registry = prometheus_client.REGISTRY
    expected = {
        "connect_http_requests_total": "connect_http_requests",
        "connect_http_request_duration_seconds": "connect_http_request_duration_seconds",
        "connect_arq_jobs_total": "connect_arq_jobs",
        "connect_arq_job_duration_seconds": "connect_arq_job_duration_seconds",
        "connect_arq_queue_depth": "connect_arq_queue_depth",
        "connect_db_pool_in_use": "connect_db_pool_in_use",
    }
    seen = {m.name for m in registry.collect()}
    missing = {
        spec_name for spec_name, family_name in expected.items() if family_name not in seen
    }
    assert not missing, f"missing from registry: {missing}"
