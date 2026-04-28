"""Repository for the User model."""

from __future__ import annotations

import uuid

from sqlalchemy import func, update as sa_update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from db.models import User
from db.repositories.base import AsyncRepository


class UserRepository(AsyncRepository[User, uuid.UUID]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def update_last_login(self, user_id: uuid.UUID) -> None:
        stmt = sa_update(User).where(User.id == user_id).values(last_login=func.now())
        await self._session.execute(stmt)
        await self._session.flush()

    async def update_password(self, user_id: uuid.UUID, password_hash: str) -> bool:
        stmt = (
            sa_update(User)
            .where(User.id == user_id)
            .values(password_hash=password_hash)
        )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0


__all__ = ["UserRepository"]
