"""Repositories for Job (import_jobs) and ProcessedJob (processed_jobs) models."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import select, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Job, ProcessedJob
from db.repositories.base import AsyncRepository


class JobRepository(AsyncRepository[Job, int]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Job)

    async def list_for_user(
        self,
        user_id: Optional[uuid.UUID],
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Job]:
        if user_id is None:
            stmt = (
                select(Job)
                .where(Job.user_id.is_(None))
                .order_by(Job.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        else:
            stmt = (
                select(Job)
                .where(Job.user_id == user_id)
                .order_by(Job.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def create_job(
        self,
        user_id: Optional[uuid.UUID],
        bucket: str,
        prefix: str,
        filespace_id: str,
        datastore_id: str,
    ) -> Job:
        return await self.create(
            user_id=user_id,
            bucket=bucket,
            prefix=prefix,
            filespace_id=filespace_id,
            datastore_id=datastore_id,
            status="pending",
        )

    async def cancel_job(self, job_id: int, user_id: Optional[uuid.UUID]) -> bool:
        """Set status to 'cancelled' only if job exists and user_id matches.

        Returns True if the row was updated, False otherwise.
        """
        if user_id is None:
            stmt = (
                sa_update(Job)
                .where(Job.id == job_id, Job.user_id.is_(None))
                .values(status="cancelled")
            )
        else:
            stmt = (
                sa_update(Job)
                .where(Job.id == job_id, Job.user_id == user_id)
                .values(status="cancelled")
            )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0

    async def update_status(
        self, job_id: int, status: str, **fields: Any
    ) -> Optional[Job]:
        """Set status and any additional fields; return the updated row or None."""
        return await self.update(job_id, status=status, **fields)

    async def timeout_stale(self, hours: int = 1) -> int:
        """Fail running jobs whose started_at is older than *hours* hours.

        Returns the count of rows updated.  Callers must commit.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        stmt = (
            sa_update(Job)
            .where(Job.status == "running", Job.started_at < cutoff)
            .values(status="failed", error_message="timed out")
        )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount


class ProcessedJobRepository(AsyncRepository[ProcessedJob, str]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProcessedJob)

    async def mark_processed(
        self,
        job_id: str,
        worker_id: Optional[str] = None,
        result_status: Optional[str] = None,
    ) -> bool:
        """Insert a processed-job record; return True if newly inserted.

        Uses ON CONFLICT (job_id) DO NOTHING so concurrent workers racing on
        the same ARQ job_id will see exactly one winner (True) and all others
        will return False without error.  Callers must commit.
        """
        stmt = (
            pg_insert(ProcessedJob)
            .values(job_id=job_id, worker_id=worker_id, result_status=result_status)
            .on_conflict_do_nothing(index_elements=["job_id"])
        )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0


__all__ = ["JobRepository", "ProcessedJobRepository"]
