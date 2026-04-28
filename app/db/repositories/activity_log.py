"""Repository for the ActivityLog model.

Backs the in-UI log views (logs_app/jobs/sqs/admin tabs). The write
path is fire-and-forget from ActivityLogger.log via a fresh session
per insert; the read path is per-route via Depends(get_db).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ActivityLog
from db.repositories.base import AsyncRepository


class ActivityLogRepository(AsyncRepository[ActivityLog, int]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ActivityLog)

    async def create_entry(
        self,
        *,
        category: str,
        action: str,
        message: str,
        level: str = "info",
        user_id: Optional[uuid.UUID] = None,
        details: Optional[dict[str, Any]] = None,
        related_id: Optional[str] = None,
        related_type: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> ActivityLog:
        return await self.create(
            category=category,
            action=action,
            message=message,
            level=level,
            user_id=user_id,
            details=details,
            related_id=related_id,
            related_type=related_type,
            ip_address=ip_address,
        )

    async def list_filtered(
        self,
        *,
        category: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        include_all_users: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ActivityLog]:
        """Return logs newest-first. When `include_all_users` is False,
        scopes to the supplied user_id (or NULL-user rows)."""
        stmt = select(ActivityLog)
        if category is not None:
            stmt = stmt.where(ActivityLog.category == category)
        if not include_all_users:
            if user_id is None:
                stmt = stmt.where(ActivityLog.user_id.is_(None))
            else:
                stmt = stmt.where(ActivityLog.user_id == user_id)
        stmt = stmt.order_by(ActivityLog.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_filtered(
        self,
        *,
        category: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        include_all_users: bool = False,
    ) -> int:
        stmt = select(func.count()).select_from(ActivityLog)
        if category is not None:
            stmt = stmt.where(ActivityLog.category == category)
        if not include_all_users:
            if user_id is None:
                stmt = stmt.where(ActivityLog.user_id.is_(None))
            else:
                stmt = stmt.where(ActivityLog.user_id == user_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def clear_for_user(
        self,
        category: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
    ) -> int:
        """Delete logs scoped to a user (and optionally a single category).
        Returns the deleted row count."""
        stmt = sa_delete(ActivityLog)
        if user_id is None:
            stmt = stmt.where(ActivityLog.user_id.is_(None))
        else:
            stmt = stmt.where(ActivityLog.user_id == user_id)
        if category is not None:
            stmt = stmt.where(ActivityLog.category == category)
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount

    async def clear_old(self, days: int = 30) -> int:
        """Delete logs older than *days*. Returns the deleted row count."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        stmt = sa_delete(ActivityLog).where(ActivityLog.created_at < cutoff)
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount


__all__ = ["ActivityLogRepository"]
