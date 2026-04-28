"""Generic async repository base class for SQLAlchemy 2.x ORM models.

Concrete repositories for User, Datastore, and Job subclass AsyncRepository
and inherit the CRUD + query primitives defined here. All I/O is async;
callers are responsible for committing the session — this layer only flushes.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import Column, delete as sa_delete
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

ModelT = TypeVar("ModelT", bound=DeclarativeBase)
IDT = TypeVar("IDT")


def _pk_column(model: type[DeclarativeBase]) -> Column[Any]:
    """Return the single primary-key Column for *model*.

    Repositories on this branch always have a single-column PK (UUID or int).
    Composite-PK models are not supported by this base class.
    """
    return model.__mapper__.primary_key[0]  # type: ignore[return-value]


class AsyncRepository(Generic[ModelT, IDT]):
    """CRUD + bounded-list primitives for a single SQLAlchemy ORM model.

    Callers own session commit lifecycle; this class only flushes after
    mutations so that the caller can compose multiple operations in one
    transaction and commit once.
    """

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    async def get(self, id: IDT) -> ModelT | None:
        return await self._session.get(self._model, id)

    async def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        order_by: InstrumentedAttribute[Any] | Column[Any] | None = None,
    ) -> list[ModelT]:
        stmt = select(self._model).limit(limit).offset(offset)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **fields: Any) -> ModelT:
        instance = self._model(**fields)
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def update(self, id: IDT, **fields: Any) -> ModelT | None:
        instance = await self._session.get(self._model, id)
        if instance is None:
            return None
        for key, value in fields.items():
            setattr(instance, key, value)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def delete(self, id: IDT) -> bool:
        pk_col = _pk_column(self._model)
        stmt = sa_delete(self._model).where(pk_col == id)
        from sqlalchemy.engine import CursorResult

        result = await self._session.execute(stmt)
        cursor: CursorResult[Any] = result  # type: ignore[assignment]
        return cursor.rowcount > 0

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(self._model)
        )
        value = result.scalar_one()
        return int(value)

    async def exists(self, id: IDT) -> bool:
        pk_col = _pk_column(self._model)
        stmt = select(exists().where(pk_col == id))
        result = await self._session.execute(stmt)
        return bool(result.scalar_one())


__all__ = ["AsyncRepository"]
