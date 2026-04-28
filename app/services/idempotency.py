"""ARQ-task idempotency helper backed by the processed_jobs table.

Wrap any ARQ task entry point with @idempotent so KEDA-induced re-invocation
(same ARQ job_id delivered to a fresh worker) is a no-op rather than a
duplicate side effect. CLAUDE.md architecture rule #7.
"""

from __future__ import annotations

import functools
import os
from typing import Any, Callable, Coroutine, TypeVar

from sqlalchemy import update as sa_update

from services.database import get_sessionmaker
from services.logging import get_logger
from db.models import ProcessedJob
from db.repositories.job import ProcessedJobRepository

R = TypeVar("R")
log = get_logger(__name__)


def idempotent(
    func: Callable[..., Coroutine[Any, Any, R]],
) -> Callable[..., Coroutine[Any, Any, R | None]]:
    """Decorator: wraps an ARQ task function with check-and-skip dedup.

    Reads ARQ's ctx['job_id'] (set by ARQ at task dispatch), atomically
    inserts a processed_jobs row, and only invokes the wrapped task if the
    insert affected a row. Re-deliveries return None without side effects.

    Failure contract: if the wrapped function raises, the processed_jobs row
    is updated to result_status='failed'. Subsequent re-deliveries with the
    same ARQ job_id are STILL skipped — the dedup check happens before the
    function body runs. This is intentional: for this project a failed import
    that is re-attempted would duplicate file ingestion into LucidLink. A
    failed job must be retried explicitly by an operator, not automatically on
    KEDA re-delivery.
    """

    @functools.wraps(func)
    async def wrapper(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> R | None:
        arq_job_id: str | None = ctx.get("job_id")
        if not arq_job_id:
            # Defensive: ARQ should always set this. If absent, run normally
            # rather than masking a misconfiguration.
            log.warning("idempotency: ctx['job_id'] missing; executing without dedup")
            return await func(ctx, *args, **kwargs)

        sessionmaker = ctx.get("sessionmaker") or get_sessionmaker()
        worker_id: str | None = os.environ.get("HOSTNAME") or os.environ.get("POD_NAME")

        async with sessionmaker() as session:
            repo = ProcessedJobRepository(session)
            newly_processed = await repo.mark_processed(
                job_id=arq_job_id,
                worker_id=worker_id,
            )
            await session.commit()

        if not newly_processed:
            log.info(
                "job already processed, skipping",
                extra={"arq_job_id": arq_job_id, "task": func.__name__},
            )
            return None

        try:
            return await func(ctx, *args, **kwargs)
        except Exception:
            # Mark as failed for forensics. The PK row was already inserted
            # above, so re-delivery with the same job_id still skips (the
            # ON CONFLICT DO NOTHING returns 0 rows on the next attempt).
            async with sessionmaker() as session:
                await session.execute(
                    sa_update(ProcessedJob)
                    .where(ProcessedJob.job_id == arq_job_id)
                    .values(result_status="failed")
                )
                await session.commit()
            raise

    return wrapper
