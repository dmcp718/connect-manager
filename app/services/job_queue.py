"""Import Job Queue Service.

Dispatches jobs to ARQ workers via Valkey/Redis and subscribes to worker
logs via pub/sub.  All DB operations use JobRepository via an injected
async_sessionmaker so callers (on_startup) control the session lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

from arq import create_pool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.repositories.job import JobRepository
from services.logging import get_logger
from services.valkey import arq_redis_settings, make_redis_client

LOG_CHANNEL = "worker:logs"
_log = get_logger("job_queue")


class JobQueue:
    """Job queue dispatcher using ARQ with log subscription."""

    def __init__(self) -> None:
        self._redis_pool: Any = None
        self._pubsub: Any = None
        self._subscriber_task: Optional[asyncio.Task[None]] = None
        self._sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None

    def set_sessionmaker(self, sm: async_sessionmaker[AsyncSession]) -> None:
        """Inject the async_sessionmaker used for all DB operations."""
        self._sessionmaker = sm

    async def start(self) -> None:
        """Initialize connection to Valkey/Redis and start log subscriber."""
        try:
            self._redis_pool = await create_pool(arq_redis_settings())
            _log.info("Job queue connected to Valkey")

            await self._start_log_subscriber()
        except Exception as e:
            _log.warning("Job queue fallback mode (no Valkey)", extra={"error": str(e)})
            self._redis_pool = None

    async def _start_log_subscriber(self) -> None:
        """Subscribe to worker log channel."""
        try:
            redis_client = make_redis_client()
            self._pubsub = redis_client.pubsub()
            await self._pubsub.subscribe(LOG_CHANNEL)
            self._subscriber_task = asyncio.create_task(self._listen_for_logs())
            _log.info("Subscribed to worker logs")
        except Exception as e:
            _log.warning("Could not subscribe to worker logs", extra={"error": str(e)})

    async def _listen_for_logs(self) -> None:
        """Listen for log messages from workers."""
        try:
            async for message in self._pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        _log.info(data.get("message", ""), extra={"source": "worker"})
                    except (json.JSONDecodeError, KeyError):
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _log.error("Log subscriber error", extra={"error": str(e)})

    async def stop(self) -> None:
        """Close connection to Valkey/Redis."""
        if self._subscriber_task:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
            self._subscriber_task = None

        if self._pubsub:
            await self._pubsub.unsubscribe(LOG_CHANNEL)
            await self._pubsub.close()
            self._pubsub = None

        if self._redis_pool:
            self._redis_pool.close()
            await self._redis_pool.wait_closed()
            self._redis_pool = None

        _log.info("Job queue disconnected")

    async def add_job(
        self,
        bucket: str,
        prefix: str,
        filespace_id: str,
        datastore_id: str,
        user_id: Optional[str] = None,
    ) -> int:
        """Add a new import job to the queue."""
        if self._sessionmaker is None:
            raise RuntimeError(
                "JobQueue.set_sessionmaker() must be called before add_job()"
            )

        parsed_user_id: Optional[uuid.UUID] = uuid.UUID(user_id) if user_id else None

        async with self._sessionmaker() as session:
            repo = JobRepository(session)
            job = await repo.create_job(
                user_id=parsed_user_id,
                bucket=bucket,
                prefix=prefix,
                filespace_id=filespace_id,
                datastore_id=datastore_id,
            )
            await session.commit()
            job_id: int = job.id

        _log.info("Job queued", extra={"job_id": job_id, "prefix": prefix})

        if self._redis_pool:
            await self._redis_pool.enqueue_job("import_job", job_id)
            _log.info("Job dispatched to worker", extra={"job_id": job_id})
        else:
            _log.warning("Job pending (no workers available)", extra={"job_id": job_id})

        return job_id

    async def cancel_job(self, job_id: int, user_id: Optional[str] = None) -> bool:
        """Cancel a job (filtered by user in multi-user mode)."""
        if self._sessionmaker is None:
            raise RuntimeError(
                "JobQueue.set_sessionmaker() must be called before cancel_job()"
            )

        parsed_user_id: Optional[uuid.UUID] = uuid.UUID(user_id) if user_id else None

        async with self._sessionmaker() as session:
            repo = JobRepository(session)
            cancelled = await repo.cancel_job(job_id, parsed_user_id)
            await session.commit()

        if cancelled:
            _log.info("Job cancelled", extra={"job_id": job_id})
        return cancelled

    async def get_jobs(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Get all jobs (filtered by user in multi-user mode)."""
        if self._sessionmaker is None:
            raise RuntimeError(
                "JobQueue.set_sessionmaker() must be called before get_jobs()"
            )

        parsed_user_id: Optional[uuid.UUID] = uuid.UUID(user_id) if user_id else None

        async with self._sessionmaker() as session:
            repo = JobRepository(session)
            jobs = await repo.list_for_user(parsed_user_id)

        return [
            {
                "id": j.id,
                "status": j.status,
                "bucket": j.bucket,
                "prefix": j.prefix,
                "filespace_id": j.filespace_id,
                "datastore_id": j.datastore_id,
                "total_files": j.total_files,
                "completed_files": j.completed_files,
                "failed_files": j.failed_files,
                "error_message": j.error_message,
                "created_at": j.created_at,
                "started_at": j.started_at,
                "completed_at": j.completed_at,
                "user_id": str(j.user_id) if j.user_id else None,
            }
            for j in jobs
        ]

    async def get_queue_status(self, user_id: Optional[str] = None) -> dict[str, Any]:
        """Get current queue status (filtered by user in multi-user mode)."""
        jobs = await self.get_jobs(user_id=user_id)
        pending = sum(1 for j in jobs if j["status"] == "pending")
        running = sum(1 for j in jobs if j["status"] == "running")

        return {
            "pending": pending,
            "running": running,
            "connected": self._redis_pool is not None,
        }


# Global job queue instance
job_queue = JobQueue()
