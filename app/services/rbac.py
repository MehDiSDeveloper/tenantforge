"""Role management, and the one place a user permission set is computed."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.permissions import Permission
from app.core.principal import Principal
from app.models.rbac import Role
from app.models.user import User
from app.repositories.rbac import RoleRepository
from app.schemas.rbac import RoleCreate, RoleRead, RoleUpdate
from app.services.audit import AuditService


def permissions_for(user: User) -> frozenset[Permission]:
    """Union of the permissions of every role the user holds.

    A permission code stored on a role that the current code no longer defines
    is dropped rather than raising: removing a permission from the enum is a
    deployment, not a data migration, and it must fail closed.
    """
    codes: set[Permission] = set()
    for role in user.roles:
        for row in role.permissions:
            try:
                codes.add(Permission(row.permission))
            except ValueError:
                continue
    return frozenset(codes)


def _to_read(role: Role) -> RoleRead:
    model = RoleRead.model_validate(role)
    return model.model_copy(update={"permissions": sorted(role.permission_codes)})


class RoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.roles = RoleRepository(session)
        self.audit = AuditService(session)

    async def list(self) -> list[RoleRead]:
        return [_to_read(role) for role in await self.roles.list_all()]

    async def get(self, role_id: UUID) -> RoleRead:
        role = await self.roles.get_with_permissions(role_id)
        if role is None:
            raise NotFoundError("Role not found.")
        return _to_read(role)

    async def create(self, principal: Principal, payload: RoleCreate) -> RoleRead:
        role = Role(
            tenant_id=principal.tenant_id,
            name=payload.name,
            description=payload.description,
            is_system=False,
        )
        try:
            await self.roles.add(role)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("A role with that name already exists.") from exc

        await self.roles.set_permissions(role, payload.permissions)
        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="role.created",
            resource_type="role",
            resource_id=str(role.id),
            actor=principal,
            changes={
                "name": role.name,
                "permissions": sorted(p.value for p in payload.permissions),
            },
        )
        return _to_read(role)

    async def update(self, principal: Principal, role_id: UUID, payload: RoleUpdate) -> RoleRead:
        role = await self.roles.get_with_permissions(role_id)
        if role is None:
            raise NotFoundError("Role not found.")
        if role.is_system:
            raise ValidationError("Built-in roles cannot be modified.")

        changes: dict[str, object] = {}
        if payload.description is not None:
            role.description = payload.description
            changes["description"] = payload.description
        if payload.permissions is not None:
            await self.roles.set_permissions(role, payload.permissions)
            changes["permissions"] = sorted(p.value for p in payload.permissions)

        await self.session.flush()
        if changes:
            await self.audit.record(
                tenant_id=principal.tenant_id,
                action="role.updated",
                resource_type="role",
                resource_id=str(role.id),
                actor=principal,
                changes=changes,
            )
        return _to_read(role)

    async def delete(self, principal: Principal, role_id: UUID) -> None:
        role = await self.roles.get_with_permissions(role_id)
        if role is None:
            raise NotFoundError("Role not found.")
        if role.is_system:
            raise ValidationError("Built-in roles cannot be deleted.")

        name = role.name
        await self.roles.delete(role)
        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="role.deleted",
            resource_type="role",
            resource_id=str(role_id),
            actor=principal,
            changes={"name": name},
        )
