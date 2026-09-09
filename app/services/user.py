"""User management inside one tenant.

Note what is absent: not a single ``if user.tenant_id != principal.tenant_id``.
A user id belonging to another tenant simply does not resolve, because the
session is bound to a tenant and the policy on ``users`` filters the row out.
The IDOR answer is therefore a 404 produced by the database, not by a check
somebody has to remember to write on every new endpoint.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.pagination import Page, PageParams
from app.core.principal import Principal
from app.core.security import hash_password
from app.models.user import User
from app.repositories.rbac import RoleRepository
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services.audit import AuditService


def _to_read(user: User) -> UserRead:
    model = UserRead.model_validate(user)
    return model.model_copy(update={"roles": sorted(role.name for role in user.roles)})


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.audit = AuditService(session)

    async def list(self, params: PageParams, query: str | None = None) -> Page[UserRead]:
        rows, total = await self.users.search(params, query)
        return Page.build([_to_read(row) for row in rows], total, params)

    async def get(self, user_id: UUID) -> UserRead:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return _to_read(user)

    async def create(self, principal: Principal, payload: UserCreate) -> UserRead:
        roles = await self.roles.list_by_names(payload.role_names)
        missing = set(payload.role_names) - {role.name for role in roles}
        if missing:
            raise ValidationError(f"Unknown roles: {', '.join(sorted(missing))}")

        user = User(
            tenant_id=principal.tenant_id,
            email=payload.email.lower(),
            full_name=payload.full_name.strip(),
            password_hash=hash_password(payload.password),
            is_active=True,
            token_version=1,
        )
        try:
            await self.users.add(user)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("A user with that email already exists.") from exc

        if roles:
            await self.users.assign_roles(user, roles)

        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="user.created",
            resource_type="user",
            resource_id=str(user.id),
            actor=principal,
            changes={"email": user.email, "roles": sorted(r.name for r in roles)},
        )
        refreshed = await self.users.get_with_roles(user.id)
        assert refreshed is not None
        return _to_read(refreshed)

    async def update(self, principal: Principal, user_id: UUID, payload: UserUpdate) -> UserRead:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        changes: dict[str, object] = {}
        if payload.full_name is not None and payload.full_name != user.full_name:
            changes["full_name"] = payload.full_name
            user.full_name = payload.full_name.strip()
        if payload.is_active is not None and payload.is_active != user.is_active:
            if user.id == principal.user_id and not payload.is_active:
                raise ValidationError("You cannot deactivate your own account.")
            changes["is_active"] = payload.is_active
            user.is_active = payload.is_active
            if not payload.is_active:
                # Deactivating has to end the session, or the user keeps
                # working until their access token expires.
                user.token_version += 1

        if changes:
            await self.session.flush()
            await self.audit.record(
                tenant_id=principal.tenant_id,
                action="user.updated",
                resource_type="user",
                resource_id=str(user.id),
                actor=principal,
                changes=changes,
            )
        return _to_read(user)

    async def set_roles(
        self, principal: Principal, user_id: UUID, role_names: Sequence[str]
    ) -> UserRead:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        roles = await self.roles.list_by_names(role_names)
        missing = set(role_names) - {role.name for role in roles}
        if missing:
            raise ValidationError(f"Unknown roles: {', '.join(sorted(missing))}")

        await self.users.assign_roles(user, roles)
        # A role change alters what live access tokens are allowed to do, and
        # permissions are read from the token holder row on every request --
        # but the token itself is still bumped so nothing cached survives.
        user.token_version += 1
        await self.session.flush()
        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="user.roles_changed",
            resource_type="user",
            resource_id=str(user.id),
            actor=principal,
            changes={"roles": sorted(role.name for role in roles)},
        )
        return _to_read(user)

    async def delete(self, principal: Principal, user_id: UUID) -> None:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if user.id == principal.user_id:
            raise ValidationError("You cannot delete your own account.")

        email = user.email
        await self.users.delete(user)
        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="user.deleted",
            resource_type="user",
            resource_id=str(user_id),
            actor=principal,
            changes={"email": email},
        )
