from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.principal import Principal
from app.repositories.tenant import TenantRepository
from app.schemas.tenant import TenantRead, TenantUpdate
from app.services.audit import AuditService


class TenantService:
    """Reads and writes the caller own tenant row.

    There is no ``tenant_id`` parameter anywhere here, and that is deliberate:
    the only tenant this session can address is the one it is bound to, so
    there is no id for a caller to tamper with.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tenants = TenantRepository(session)
        self.audit = AuditService(session)

    async def get_current(self, principal: Principal) -> TenantRead:
        tenant = await self.tenants.get(principal.tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant not found.")
        return TenantRead.model_validate(tenant)

    async def update_current(self, principal: Principal, payload: TenantUpdate) -> TenantRead:
        tenant = await self.tenants.get(principal.tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant not found.")
        if payload.name is not None and payload.name != tenant.name:
            previous, tenant.name = tenant.name, payload.name.strip()
            await self.session.flush()
            await self.audit.record(
                tenant_id=principal.tenant_id,
                action="tenant.updated",
                resource_type="tenant",
                resource_id=str(tenant.id),
                actor=principal,
                changes={"name": {"from": previous, "to": tenant.name}},
            )
        return TenantRead.model_validate(tenant)
