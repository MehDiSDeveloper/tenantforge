from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.models.tenant import Tenant
from app.repositories.base import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    model = Tenant

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return await self.session.scalar(select(Tenant).where(Tenant.slug == slug.lower()))

    async def slug_exists(self, slug: str) -> bool:
        found = await self.session.scalar(select(Tenant.id).where(Tenant.slug == slug.lower()))
        return found is not None

    async def get_active(self, tenant_id: UUID) -> Tenant | None:
        return await self.session.scalar(
            select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
        )
