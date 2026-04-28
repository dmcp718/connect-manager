"""Repositories for DatastoreCredentials, SqsCredentials, SqsQueue, and SqsEvent.

Encryption contract (CLAUDE.md rule #5, SPEC §5.1):
  - Models hold Fernet ciphertext (bytes) in LargeBinary columns.
  - Encryption/decryption happens at the service layer (services/state.py,
    services/user_state.py) via services/secrets.py — never here.
  - These repositories accept and return raw ciphertext; callers are
    responsible for encrypt-before-write and decrypt-after-read.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import delete as sa_delete, func, select, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import DatastoreCredentials, SqsCredentials, SqsEvent, SqsQueue
from db.repositories.base import AsyncRepository


class DatastoreCredentialsRepository(AsyncRepository[DatastoreCredentials, int]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DatastoreCredentials)

    async def list_for_user(
        self, user_id: Optional[uuid.UUID]
    ) -> Sequence[DatastoreCredentials]:
        if user_id is None:
            stmt = select(DatastoreCredentials).where(
                DatastoreCredentials.user_id.is_(None)
            )
        else:
            stmt = select(DatastoreCredentials).where(
                DatastoreCredentials.user_id == user_id
            )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_for_datastore_user(
        self, datastore_id: str, user_id: Optional[uuid.UUID]
    ) -> Optional[DatastoreCredentials]:
        if user_id is None:
            stmt = select(DatastoreCredentials).where(
                DatastoreCredentials.datastore_id == datastore_id,
                DatastoreCredentials.user_id.is_(None),
            )
        else:
            stmt = select(DatastoreCredentials).where(
                DatastoreCredentials.datastore_id == datastore_id,
                DatastoreCredentials.user_id == user_id,
            )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        datastore_id: str,
        datastore_name: str,
        filespace_id: str,
        filespace_name: str,
        bucket_name: str,
        credentials_key: bytes,
        region: Optional[str] = None,
        endpoint: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
    ) -> DatastoreCredentials:
        """Insert or update by (datastore_id, user_id) unique constraint."""
        values: dict[str, object] = {
            "datastore_id": datastore_id,
            "datastore_name": datastore_name,
            "filespace_id": filespace_id,
            "filespace_name": filespace_name,
            "bucket_name": bucket_name,
            "credentials_key": credentials_key,
            "region": region,
            "endpoint": endpoint,
            "user_id": user_id,
        }
        stmt = (
            pg_insert(DatastoreCredentials)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_datastore_credentials_datastore_user",
                set_={
                    "datastore_name": datastore_name,
                    "filespace_id": filespace_id,
                    "filespace_name": filespace_name,
                    "bucket_name": bucket_name,
                    "credentials_key": credentials_key,
                    "region": region,
                    "endpoint": endpoint,
                },
            )
            .returning(DatastoreCredentials)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one()
        return row

    async def delete_for_datastore_user(
        self, datastore_id: str, user_id: Optional[uuid.UUID]
    ) -> bool:
        if user_id is None:
            stmt = sa_delete(DatastoreCredentials).where(
                DatastoreCredentials.datastore_id == datastore_id,
                DatastoreCredentials.user_id.is_(None),
            )
        else:
            stmt = sa_delete(DatastoreCredentials).where(
                DatastoreCredentials.datastore_id == datastore_id,
                DatastoreCredentials.user_id == user_id,
            )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        return cursor.rowcount > 0


class SqsCredentialsRepository(AsyncRepository[SqsCredentials, int]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SqsCredentials)

    async def list_for_user(
        self, user_id: Optional[uuid.UUID]
    ) -> Sequence[SqsCredentials]:
        if user_id is None:
            stmt = select(SqsCredentials).where(SqsCredentials.user_id.is_(None))
        else:
            stmt = select(SqsCredentials).where(SqsCredentials.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_for_user(
        self, user_id: Optional[uuid.UUID]
    ) -> Optional[SqsCredentials]:
        """Return the single SqsCredentials row for a user (or NULL user)."""
        rows = await self.list_for_user(user_id)
        return rows[0] if rows else None

    async def upsert_for_user(
        self,
        access_key: str,
        secret_key_encrypted: bytes,
        region: str,
        user_id: Optional[uuid.UUID],
    ) -> SqsCredentials:
        """Replace this user's SQS credentials with a single fresh row.

        Legacy contract on main was "delete then insert" because the table
        has no unique constraint on user_id. Preserved here.
        """
        await self.delete_for_user(user_id)
        instance = SqsCredentials(
            access_key=access_key,
            secret_key_encrypted=secret_key_encrypted,
            region=region,
            user_id=user_id,
        )
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def delete_for_user(self, user_id: Optional[uuid.UUID]) -> int:
        """Delete all SQS credentials for *user_id*. Returns the deleted count."""
        if user_id is None:
            stmt = sa_delete(SqsCredentials).where(SqsCredentials.user_id.is_(None))
        else:
            stmt = sa_delete(SqsCredentials).where(SqsCredentials.user_id == user_id)
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        return cursor.rowcount


class SqsQueueRepository(AsyncRepository[SqsQueue, uuid.UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SqsQueue)

    async def list_for_user(self, user_id: Optional[uuid.UUID]) -> Sequence[SqsQueue]:
        if user_id is None:
            stmt = (
                select(SqsQueue)
                .where(SqsQueue.user_id.is_(None))
                .order_by(SqsQueue.created_at.desc())
            )
        else:
            stmt = (
                select(SqsQueue)
                .where(SqsQueue.user_id == user_id)
                .order_by(SqsQueue.created_at.desc())
            )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_for_user(
        self, queue_id: uuid.UUID, user_id: Optional[uuid.UUID]
    ) -> Optional[SqsQueue]:
        """Return *queue_id* only if it belongs to *user_id* (or to NULL).

        Multi-user scoping mirrors the legacy `get_sqs_queue(queue_id, user_id)`.
        """
        if user_id is None:
            stmt = select(SqsQueue).where(
                SqsQueue.id == queue_id, SqsQueue.user_id.is_(None)
            )
        else:
            stmt = select(SqsQueue).where(
                SqsQueue.id == queue_id, SqsQueue.user_id == user_id
            )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self) -> Sequence[SqsQueue]:
        """Return all queues with status='active' across users.

        Used by the polling cron job which must process every active queue
        regardless of owner. Ordered by created_at to make polling fair.
        """
        stmt = (
            select(SqsQueue)
            .where(SqsQueue.status == "active")
            .order_by(SqsQueue.created_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update_status_for_user(
        self,
        queue_id: uuid.UUID,
        user_id: Optional[uuid.UUID],
        status: str,
        error_message: Optional[str] = None,
    ) -> bool:
        """Set status (and optionally clear error_message) for a user-owned queue."""
        values: dict[str, object] = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        if user_id is None:
            stmt = (
                sa_update(SqsQueue)
                .where(SqsQueue.id == queue_id, SqsQueue.user_id.is_(None))
                .values(**values)
            )
        else:
            stmt = (
                sa_update(SqsQueue)
                .where(SqsQueue.id == queue_id, SqsQueue.user_id == user_id)
                .values(**values)
            )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0

    async def delete_for_user(
        self, queue_id: uuid.UUID, user_id: Optional[uuid.UUID]
    ) -> bool:
        """Delete a queue scoped to *user_id*. ON DELETE CASCADE on sqs_events
        handles the child rows."""
        if user_id is None:
            stmt = sa_delete(SqsQueue).where(
                SqsQueue.id == queue_id, SqsQueue.user_id.is_(None)
            )
        else:
            stmt = sa_delete(SqsQueue).where(
                SqsQueue.id == queue_id, SqsQueue.user_id == user_id
            )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0

    async def mark_polled(
        self,
        queue_id: uuid.UUID,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update last_poll_at to now and set or clear error_message."""
        stmt = (
            sa_update(SqsQueue)
            .where(SqsQueue.id == queue_id)
            .values(
                last_poll_at=datetime.now(tz=timezone.utc),
                error_message=error_message,
            )
        )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0

    async def set_error(
        self, queue_id: uuid.UUID, error_message: Optional[str]
    ) -> bool:
        """Set or clear the error_message field. No user scoping — used by the
        polling cron which iterates list_active()."""
        stmt = (
            sa_update(SqsQueue)
            .where(SqsQueue.id == queue_id)
            .values(error_message=error_message)
        )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0


class SqsEventRepository(AsyncRepository[SqsEvent, uuid.UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SqsEvent)

    async def create_event(
        self,
        queue_id: uuid.UUID,
        message_id: str,
        event_type: str,
        bucket: str,
        object_key: str,
        object_size: Optional[int] = None,
        event_time: Optional[datetime] = None,
        status: str = "pending",
    ) -> SqsEvent:
        return await self.create(
            queue_id=queue_id,
            message_id=message_id,
            event_type=event_type,
            bucket=bucket,
            object_key=object_key,
            object_size=object_size,
            event_time=event_time,
            status=status,
        )

    async def exists_for_message(
        self, message_id: str, queue_id: uuid.UUID, object_key: str
    ) -> bool:
        """Idempotency probe: has this exact (message_id, queue_id, object_key)
        already been recorded? Backed by idx_sqs_events_message_id."""
        stmt = (
            select(SqsEvent.id)
            .where(
                SqsEvent.message_id == message_id,
                SqsEvent.queue_id == queue_id,
                SqsEvent.object_key == object_key,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def update_status(
        self,
        event_id: uuid.UUID,
        status: Optional[str] = None,
        job_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Patch status / job_id / error_message on an event row."""
        values: dict[str, object] = {}
        if status is not None:
            values["status"] = status
        if job_id is not None:
            values["job_id"] = job_id
        if error_message is not None:
            values["error_message"] = error_message
        if not values:
            return False
        stmt = sa_update(SqsEvent).where(SqsEvent.id == event_id).values(**values)
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0

    async def list_for_user(
        self, user_id: Optional[uuid.UUID], limit: int = 50
    ) -> Sequence[SqsEvent]:
        """Recent events for queues owned by *user_id*. Eager-loads `queue` so
        callers can read queue.name without a follow-up query (legacy
        list_sqs_events flattened queue.name into the returned dict)."""
        stmt = (
            select(SqsEvent)
            .options(selectinload(SqsEvent.queue))
            .join(SqsQueue, SqsEvent.queue_id == SqsQueue.id)
        )
        if user_id is None:
            stmt = stmt.where(SqsQueue.user_id.is_(None))
        else:
            stmt = stmt.where(SqsQueue.user_id == user_id)
        stmt = stmt.order_by(SqsEvent.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_today_for_queue(
        self, queue_id: uuid.UUID, user_id: Optional[uuid.UUID]
    ) -> int:
        """Count events created today for *queue_id*, gated by user ownership.

        Uses a date-trunc on created_at; database is UTC, so "today" matches
        the legacy SQLite `date('now')` behavior closely enough for the
        in-UI counter."""
        today = func.date_trunc("day", func.now())
        stmt = (
            select(func.count())
            .select_from(SqsEvent)
            .join(SqsQueue, SqsEvent.queue_id == SqsQueue.id)
            .where(
                SqsEvent.queue_id == queue_id,
                func.date_trunc("day", SqsEvent.created_at) == today,
            )
        )
        if user_id is None:
            stmt = stmt.where(SqsQueue.user_id.is_(None))
        else:
            stmt = stmt.where(SqsQueue.user_id == user_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def clear_old(self, days: int = 7) -> int:
        """Bulk-delete events older than *days* days. Returns deleted count."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        stmt = sa_delete(SqsEvent).where(SqsEvent.created_at < cutoff)
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount

    async def clear_for_user(self, user_id: Optional[uuid.UUID]) -> int:
        """Delete every event for queues owned by *user_id*."""
        if user_id is None:
            sub = select(SqsQueue.id).where(SqsQueue.user_id.is_(None))
        else:
            sub = select(SqsQueue.id).where(SqsQueue.user_id == user_id)
        stmt = sa_delete(SqsEvent).where(SqsEvent.queue_id.in_(sub))
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount


__all__ = [
    "DatastoreCredentialsRepository",
    "SqsCredentialsRepository",
    "SqsEventRepository",
    "SqsQueueRepository",
]
