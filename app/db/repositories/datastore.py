"""Repositories for DatastoreCredentials, SqsCredentials, and SqsQueue models.

Encryption contract (CLAUDE.md rule #5, SPEC §5.1):
  - Models hold Fernet ciphertext (bytes) in LargeBinary columns.
  - Encryption/decryption happens at the service layer (services/state.py,
    services/user_state.py) via services/secrets.py — never here.
  - These repositories accept and return raw ciphertext; callers are
    responsible for encrypt-before-write and decrypt-after-read.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import delete as sa_delete, select, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DatastoreCredentials, SqsCredentials, SqsQueue
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
            stmt = select(SqsQueue).where(SqsQueue.user_id.is_(None))
        else:
            stmt = select(SqsQueue).where(SqsQueue.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

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


__all__ = [
    "DatastoreCredentialsRepository",
    "SqsCredentialsRepository",
    "SqsQueueRepository",
]
