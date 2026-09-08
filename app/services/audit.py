"""Writing the audit trail.

One door in, so the four things a call site would forget -- the tenant, the
actor, the request id and the client address -- are filled in from the
principal rather than passed by hand.

The row is added to the *caller* transaction and never committed here: an
audit entry that survived a rolled-back mutation would be a record of
something that did not happen.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, log_event
from app.core.pagination import Page, PageParams
from app.core.principal import Principal
from app.models.audit import AuditLog
from app.repositories.audit import AuditRepository
from app.schemas.audit import AuditLogRead

logger = get_logger(__name__)


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AuditRepository(session)

    async def record(
        self,
        *,
        tenant_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        actor: Principal | None = None,
        actor_email: str | None = None,
        changes: dict[str, Any] | None = None,
        ip_address: str | None = None,
        request_id: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor.user_id if actor else None,
            actor_email=actor.email if actor else actor_email,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            ip_address=ip_address,
            request_id=request_id,
            changes=changes or {},
        )
        self.session.add(entry)
        log_event(
            logger,
            action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
        )
        return entry

    async def search(
        self,
        params: PageParams,
        *,
        action: str | None = None,
        resource_type: str | None = None,
    ) -> Page[AuditLogRead]:
        rows, total = await self.repo.search(params, action=action, resource_type=resource_type)
        return Page.build([AuditLogRead.model_validate(row) for row in rows], total, params)
