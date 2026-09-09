from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from app.core.pagination import PageParams
from app.models.domain import Order, OrderStatus
from app.repositories.base import BaseRepository


class OrderRepository(BaseRepository[Order]):
    model = Order

    def _base(self) -> Select[tuple[Order]]:
        return select(Order).options(selectinload(Order.items))

    async def get_with_items(self, order_id: UUID) -> Order | None:
        order: Order | None = await self.session.scalar(self._base().where(Order.id == order_id))
        return order

    async def search(
        self,
        params: PageParams,
        *,
        customer_id: UUID | None = None,
        status: OrderStatus | None = None,
    ) -> tuple[list[Order], int]:
        stmt = self._base()
        if customer_id:
            stmt = stmt.where(Order.customer_id == customer_id)
        if status:
            stmt = stmt.where(Order.status == status.value)
        total = await self.count(stmt)
        stmt = stmt.order_by(Order.created_at.desc(), Order.id.desc())
        return await self.paginate(stmt, params), total

    async def next_reference(self) -> str:
        """A per-tenant sequential reference.

        Counted under RLS, so the number is the tenant own count and two
        tenants both get ORD-00001 without ever seeing each other rows.
        """
        count = int(await self.session.scalar(select(func.count()).select_from(Order)) or 0)
        return f"ORD-{count + 1:05d}"
