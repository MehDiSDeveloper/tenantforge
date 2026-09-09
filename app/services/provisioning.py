"""Tenant provisioning -- the one operation with no tenant context to run in.

Everything else in the application runs inside a row-level-security session
bound to one tenant. Creating a tenant obviously cannot: the row that defines
the boundary does not exist yet. So this module, and only this module, uses the
owner-role session from :func:`app.core.db.system_session`.

Keeping it in a file of its own is the point. "Which code can see across
tenants?" has a one-word answer, and code review can hold that line.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger, log_event
from app.core.permissions import (
    SYSTEM_ROLE_DESCRIPTIONS,
    SYSTEM_ROLE_PERMISSIONS,
    SystemRole,
)
from app.core.security import hash_password
from app.models.rbac import Role, RolePermission, UserRole
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import TenantRegistration

logger = get_logger(__name__)

RESERVED_SLUGS = frozenset({"api", "admin", "www", "app", "static", "docs", "health"})


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    tenant_id: UUID
    tenant_slug: str
    owner_user_id: UUID


class ProvisioningService:
    """Runs against the owner-role session, never a request-scoped one."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_slug(self, slug: str) -> Tenant:
        """Slug -> tenant, the lookup login needs before a context can exist."""
        tenant = await self.session.scalar(
            select(Tenant).where(Tenant.slug == slug.strip().lower())
        )
        if tenant is None or not tenant.is_active:
            raise NotFoundError("Unknown or inactive tenant.")
        return tenant

    async def register(self, payload: TenantRegistration) -> ProvisionedTenant:
        slug = payload.tenant_slug.strip().lower()
        if slug in RESERVED_SLUGS:
            raise ConflictError("That workspace address is reserved.")

        tenant = Tenant(slug=slug, name=payload.tenant_name.strip(), is_active=True)
        self.session.add(tenant)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("That workspace address is already taken.") from exc

        roles = await self._seed_system_roles(tenant.id)

        owner = User(
            tenant_id=tenant.id,
            email=payload.email.lower(),
            full_name=payload.full_name.strip(),
            password_hash=hash_password(payload.password),
            is_active=True,
            token_version=1,
        )
        self.session.add(owner)
        await self.session.flush()

        self.session.add(
            UserRole(tenant_id=tenant.id, user_id=owner.id, role_id=roles[SystemRole.OWNER].id)
        )
        await self.session.flush()

        log_event(
            logger,
            "tenant.provisioned",
            tenant_id=str(tenant.id),
            tenant_slug=tenant.slug,
            owner_user_id=str(owner.id),
        )
        return ProvisionedTenant(
            tenant_id=tenant.id, tenant_slug=tenant.slug, owner_user_id=owner.id
        )

    async def _seed_system_roles(self, tenant_id: UUID) -> dict[SystemRole, Role]:
        created: dict[SystemRole, Role] = {}
        for system_role, permissions in SYSTEM_ROLE_PERMISSIONS.items():
            role = Role(
                tenant_id=tenant_id,
                name=system_role.value,
                description=SYSTEM_ROLE_DESCRIPTIONS[system_role],
                is_system=True,
            )
            self.session.add(role)
            await self.session.flush()
            for permission in sorted(permissions):
                self.session.add(
                    RolePermission(
                        tenant_id=tenant_id, role_id=role.id, permission=permission.value
                    )
                )
            created[system_role] = role
        await self.session.flush()
        return created
