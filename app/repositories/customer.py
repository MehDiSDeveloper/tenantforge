from __future__ import annotations

from sqlalchemy import func, select

from app.core.pagination import PageParams
from app.models.domain import Customer
from app.repositories.base import BaseRepository


class CustomerRepository(BaseRepository[Customer]):
    model = Customer

    async def get_by_email(self, email: str) -> Customer | None:
        return await self.session.scalar(select(Customer).where(Customer.email == email.lower()))

    async def search(
        self, params: PageParams, query: str | None = None
    ) -> tuple[list[Customer], int]:
        stmt = select(Customer)
        if query:
            pattern = f"%{query.lower()}%"
            stmt = stmt.where(
                func.lower(Customer.name).like(pattern)
                | func.lower(Customer.email).like(pattern)
                | func.lower(func.coalesce(Customer.company, "")).like(pattern)
            )
        total = await self.count(stmt)
        stmt = stmt.order_by(Customer.created_at.desc(), Customer.id.desc())
        return await self.paginate(stmt, params), total
