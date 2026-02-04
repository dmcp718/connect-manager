"""
Import Job Queue Service
Dispatches jobs to ARQ workers via Valkey/Redis
Subscribes to worker logs via pub/sub
"""

import asyncio
import json
import os
from typing import Optional, TYPE_CHECKING

from arq import create_pool
from arq.connections import RedisSettings
import redis.asyncio as redis

from services import database as db

if TYPE_CHECKING:
    from services.state import AppState

LOG_CHANNEL = "worker:logs"


def get_redis_settings() -> RedisSettings:
    return RedisSettings(
        host=os.getenv("VALKEY_HOST", "localhost"),
        port=int(os.getenv("VALKEY_PORT", 6379)),
    )


class JobQueue:
    """Job queue dispatcher using ARQ with log subscription."""

    def __init__(self):
        self._redis_pool = None
        self._pubsub = None
        self._subscriber_task: Optional[asyncio.Task] = None
        self._state: Optional["AppState"] = None

    def set_state(self, state: "AppState") -> None:
        """Set reference to app state for logging."""
        self._state = state

    def log(self, message: str) -> None:
        """Log a message via app state."""
        if self._state:
            self._state.log(message)

    async def start(self) -> None:
        """Initialize connection to Valkey/Redis and start log subscriber."""
        try:
            self._redis_pool = await create_pool(get_redis_settings())
            self.log("📋 Job queue connected to Valkey")

            # Start log subscriber
            await self._start_log_subscriber()
        except Exception as e:
            self.log(f"⚠️ Job queue fallback mode (no Valkey): {e}")
            self._redis_pool = None

    async def _start_log_subscriber(self) -> None:
        """Subscribe to worker log channel."""
        try:
            redis_client = redis.Redis(
                host=os.getenv("VALKEY_HOST", "localhost"),
                port=int(os.getenv("VALKEY_PORT", 6379)),
            )
            self._pubsub = redis_client.pubsub()
            await self._pubsub.subscribe(LOG_CHANNEL)
            self._subscriber_task = asyncio.create_task(self._listen_for_logs())
            self.log("📋 Subscribed to worker logs")
        except Exception as e:
            self.log(f"⚠️ Could not subscribe to worker logs: {e}")

    async def _listen_for_logs(self) -> None:
        """Listen for log messages from workers."""
        try:
            async for message in self._pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        self.log(data.get("message", ""))
                    except (json.JSONDecodeError, KeyError):
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log(f"⚠️ Log subscriber error: {e}")

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

        self.log("📋 Job queue disconnected")

    async def add_job(
        self,
        bucket: str,
        prefix: str,
        filespace_id: str,
        datastore_id: str,
        user_id: Optional[str] = None,
    ) -> int:
        """Add a new import job to the queue."""
        # Create job in database
        job_id = db.create_job(
            bucket=bucket,
            prefix=prefix,
            filespace_id=filespace_id,
            datastore_id=datastore_id,
            user_id=user_id,
        )
        self.log(f"📋 Job #{job_id} queued: {prefix}")

        # Dispatch to ARQ worker
        if self._redis_pool:
            await self._redis_pool.enqueue_job("import_job", job_id)
            self.log(f"📋 Job #{job_id} dispatched to worker")
        else:
            self.log(f"⚠️ Job #{job_id} pending (no workers available)")

        return job_id

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a job."""
        if db.cancel_job(job_id):
            self.log(f"🛑 Job #{job_id} cancelled")
            return True
        return False

    def get_jobs(self) -> list:
        """Get all jobs."""
        return db.list_jobs()

    def get_queue_status(self) -> dict:
        """Get current queue status."""
        jobs = db.list_jobs(limit=100)
        pending = sum(1 for j in jobs if j["status"] == "pending")
        running = sum(1 for j in jobs if j["status"] == "running")

        return {
            "pending": pending,
            "running": running,
            "connected": self._redis_pool is not None,
        }


# Global job queue instance
job_queue = JobQueue()
