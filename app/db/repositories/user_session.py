"""Repository for the UserSession model."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import update as sa_update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from db.models import UserSession
from db.repositories.base import AsyncRepository


class UserSessionRepository(AsyncRepository[UserSession, uuid.UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserSession)

    async def create_session(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        expires_at: datetime,
    ) -> UserSession:
        instance = UserSession(
            id=session_id,
            user_id=user_id,
            expires_at=expires_at,
            revoked=False,
        )
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def get_active_session(self, session_id: uuid.UUID) -> UserSession | None:
        now = datetime.now(tz=timezone.utc)
        result = await self._session.execute(
            select(UserSession).where(
                UserSession.id == session_id,
                UserSession.revoked.is_(False),
                UserSession.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def revoke_session(self, session_id: uuid.UUID) -> bool:
        stmt = (
            sa_update(UserSession)
            .where(UserSession.id == session_id)
            .values(revoked=True)
        )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0

    async def revoke_all_user_sessions(self, user_id: uuid.UUID) -> int:
        stmt = (
            sa_update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked.is_(False))
            .values(revoked=True)
        )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount


__all__ = ["UserSessionRepository"]
