from __future__ import annotations

from sqlalchemy import Select, select

from app.core.pagination import PageParams
from app.models.audit import AuditLog
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    model = AuditLog

    def _filtered(self, action: str | None, resource_type: str | None) -> Select[tuple[AuditLog]]:
        stmt = select(AuditLog)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if resource_type:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        return stmt

    async def search(
        self,
        params: PageParams,
        *,
        action: str | None = None,
        resource_type: str | None = None,
    ) -> tuple[list[AuditLog], int]:
        stmt = self._filtered(action, resource_type)
        total = await self.count(stmt)
        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        return await self.paginate(stmt, params), total
