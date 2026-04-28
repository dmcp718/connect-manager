"""Repository for the UserSetting model — per-user key/value store.

Backs `api_host` persistence (and any future small per-user setting that
doesn't warrant a dedicated table). Composite PK (user_id, key);
user_id is required (the legacy single-user/global tier was a SQLite
artifact and is not modelled here).
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UserSetting


class UserSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID, key: str) -> Optional[str]:
        """Return the setting value or None if not set."""
        stmt = select(UserSetting.value).where(
            UserSetting.user_id == user_id, UserSetting.key == key
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(self, user_id: uuid.UUID, key: str, value: str) -> None:
        """INSERT … ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value."""
        stmt = (
            pg_insert(UserSetting)
            .values(user_id=user_id, key=key, value=value)
            .on_conflict_do_update(
                index_elements=["user_id", "key"],
                set_={"value": value},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def delete(self, user_id: uuid.UUID, key: str) -> bool:
        """Remove a single setting. Returns True if a row was removed."""
        stmt = sa_delete(UserSetting).where(
            UserSetting.user_id == user_id, UserSetting.key == key
        )
        raw = await self._session.execute(stmt)
        cursor: CursorResult[tuple[()]] = raw  # type: ignore[assignment]
        await self._session.flush()
        return cursor.rowcount > 0


__all__ = ["UserSettingsRepository"]
