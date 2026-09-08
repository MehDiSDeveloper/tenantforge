"""The data-access layer.

Repositories do not filter by tenant. That is the whole point of the design:
the tenant predicate is a PostgreSQL policy attached to the table, so a query
written here without a ``WHERE tenant_id = ...`` returns the caller tenant rows
and nothing else, and an ``INSERT`` carrying the wrong ``tenant_id`` is
rejected by the policy ``WITH CHECK`` clause rather than quietly stored.

What repositories *do* is keep SQLAlchemy out of the service layer, so business
logic can be tested against a fake without a database.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: UUID) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()

    async def count(self, stmt: Select[Any] | None = None) -> int:
        base = stmt if stmt is not None else select(self.model)
        subquery = base.with_only_columns(func.count()).order_by(None)
        return int(await self.session.scalar(subquery) or 0)

    async def paginate(self, stmt: Select[tuple[ModelT]], params: PageParams) -> list[ModelT]:
        result = await self.session.scalars(stmt.limit(params.limit).offset(params.offset))
        return list(result.unique())
