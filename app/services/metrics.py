"""Prometheus instrumentation primitives for ARQ jobs, the ARQ queue, and the DB pool.

Three helpers live here:

  * :func:`instrumented` — wraps an ARQ task entry point so each invocation
    increments ``connect_arq_jobs_total`` and observes
    ``connect_arq_job_duration_seconds``.

  * :func:`start_metrics_sampler` / :func:`stop_metrics_sampler` — manage a
    1Hz background task that reads the ARQ queue depth from Valkey and the
    SQLAlchemy pool's ``checkedout()`` count, updating
    ``connect_arq_queue_depth`` and ``connect_db_pool_in_use`` respectively.

The wrapper composes with :func:`services.idempotency.idempotent` (apply
``@instrumented`` on top so duration covers the dedup check too — that work
is part of the per-job cost we want visibility into).
"""

from __future__ import annotations

import asyncio
import functools
import os
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncEngine

from routes.health import (
    arq_job_duration_seconds,
    arq_jobs_total,
    arq_queue_depth,
    db_pool_in_use,
)
from services.logging import get_logger
from services.valkey import make_redis_client

R = TypeVar("R")
log = get_logger(__name__)


# ARQ queue key inside Valkey/Redis. ARQ's default queue is named "arq:queue"
# and ``llen`` against it returns the number of pending jobs. Override via
# ``ARQ_QUEUE_NAME`` if a custom queue name is configured.
_DEFAULT_QUEUE_NAME = os.environ.get("ARQ_QUEUE_NAME", "arq:queue")

_sampler_task: asyncio.Task[None] | None = None


def instrumented(
    func: Callable[..., Awaitable[R]],
    *,
    job_type: str | None = None,
) -> Callable[..., Awaitable[R]]:
    """Decorate an ARQ task to record outcome and duration metrics.

    ``job_type`` defaults to the wrapped function's ``__name__`` so metrics
    grouped by job type line up with the ARQ task registration. The decorator
    classifies success/failure by whether the wrapped coroutine raised — a
    coroutine that returns a dict like ``{"status": "failed"}`` is still
    counted as ``success`` here because no exception escaped; the application
    path treats those as terminal-but-handled outcomes.
    """
    label = job_type or func.__name__

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> R:
        start = time.perf_counter()
        outcome = "success"
        try:
            return await func(*args, **kwargs)
        except Exception:
            outcome = "failure"
            raise
        finally:
            elapsed = time.perf_counter() - start
            arq_jobs_total.labels(job_type=label, outcome=outcome).inc()
            arq_job_duration_seconds.labels(job_type=label).observe(elapsed)

    return wrapper


async def _sample_once(
    redis_client: "redis.Redis | None",
    engine: AsyncEngine | None,
    queue_name: str,
) -> None:
    """One pass of the sampler — exposed for tests."""
    if redis_client is not None:
        try:
            # redis.asyncio.Redis.llen returns Awaitable[int] at runtime; the
            # type stub overload picks the sync return path, so cast.
            depth = await redis_client.llen(queue_name)  # type: ignore[misc]
            arq_queue_depth.labels(queue=queue_name).set(int(depth))
        except Exception as exc:  # pragma: no cover — degraded mode
            log.warning("queue-depth sample failed", extra={"error": str(exc)})

    if engine is not None:
        try:
            # SQLAlchemy's pool exposes a sync API; checkedout() is cheap.
            pool = engine.pool
            checked_out = pool.checkedout()  # type: ignore[attr-defined]
            db_pool_in_use.set(int(checked_out))
        except Exception as exc:  # pragma: no cover — degraded mode
            log.warning("db-pool sample failed", extra={"error": str(exc)})


async def _sampler_loop(
    redis_client: "redis.Redis",
    engine: AsyncEngine | None,
    queue_name: str,
    interval: float,
) -> None:
    try:
        while True:
            await _sample_once(redis_client, engine, queue_name)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return
    finally:
        try:
            await redis_client.aclose()
        except Exception:
            pass


def start_metrics_sampler(
    *,
    valkey_host: str | None = None,
    valkey_port: int | None = None,
    queue_name: str = _DEFAULT_QUEUE_NAME,
    interval: float = 1.0,
    engine: AsyncEngine | None = None,
) -> Optional[asyncio.Task[None]]:
    """Start the 1Hz background sampler. Idempotent; returns the task."""
    global _sampler_task
    if _sampler_task is not None and not _sampler_task.done():
        return _sampler_task

    try:
        if valkey_host is not None or valkey_port is not None:
            # Caller passed an explicit override (test path) — honor it.
            client: "redis.Redis" = redis.Redis(
                host=valkey_host or os.environ.get("VALKEY_HOST", "localhost"),
                port=valkey_port or int(os.environ.get("VALKEY_PORT", "6379")),
                socket_timeout=2.0,
            )
        else:
            client = make_redis_client(socket_timeout=2.0)
    except Exception as exc:
        log.warning("metrics sampler: redis init failed", extra={"error": str(exc)})
        return None

    _sampler_task = asyncio.create_task(
        _sampler_loop(client, engine, queue_name, interval),
        name="connect-metrics-sampler",
    )
    return _sampler_task


async def stop_metrics_sampler() -> None:
    """Cancel and await the sampler task; safe to call multiple times."""
    global _sampler_task
    if _sampler_task is None:
        return
    task = _sampler_task
    _sampler_task = None
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
