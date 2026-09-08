from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.permissions import Permission
from app.models.rbac import Role, RolePermission
from app.repositories.base import BaseRepository


class RoleRepository(BaseRepository[Role]):
    model = Role

    async def get_with_permissions(self, role_id: UUID) -> Role | None:
        return await self.session.scalar(
            select(Role).options(selectinload(Role.permissions)).where(Role.id == role_id)
        )

    async def get_by_name(self, name: str) -> Role | None:
        return await self.session.scalar(
            select(Role).options(selectinload(Role.permissions)).where(Role.name == name)
        )

    async def list_by_names(self, names: Iterable[str]) -> list[Role]:
        wanted = list(names)
        if not wanted:
            return []
        result = await self.session.scalars(
            select(Role).options(selectinload(Role.permissions)).where(Role.name.in_(wanted))
        )
        return list(result)

    async def list_all(self) -> list[Role]:
        result = await self.session.scalars(
            select(Role).options(selectinload(Role.permissions)).order_by(Role.name)
        )
        return list(result)

    async def set_permissions(self, role: Role, permissions: Iterable[Permission]) -> Role:
        wanted = {Permission(p).value for p in permissions}
        current = list(role.permissions)
        for row in current:
            if row.permission not in wanted:
                await self.session.delete(row)
        have = {row.permission for row in current}
        for permission in sorted(wanted):
            if permission not in have:
                self.session.add(
                    RolePermission(
                        tenant_id=role.tenant_id, role_id=role.id, permission=permission
                    )
                )
        await self.session.flush()
        await self.session.refresh(role, attribute_names=["permissions"])
        return role
