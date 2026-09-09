"""Seed two workspaces with data, so the isolation is visible by hand.

    python -m scripts.seed

Two tenants exist on purpose: a demo of a multi-tenant system with one tenant
demonstrates nothing. Sign in as each and watch the same endpoints return
different rows.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from uuid import UUID

from app.core.db import app_session, dispose_engines, system_session
from app.core.logging import configure_logging
from app.core.permissions import SystemRole
from app.core.security import hash_password
from app.models.domain import Customer, Order, OrderItem, OrderStatus
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.repositories.rbac import RoleRepository
from app.schemas.auth import TenantRegistration
from app.services.provisioning import ProvisioningService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEMO_PASSWORD = "correct-horse-battery-staple"  # noqa: S105 - a seed fixture, not a secret


@dataclass(frozen=True, slots=True)
class Workspace:
    slug: str
    name: str
    owner_email: str
    customers: tuple[tuple[str, str, str], ...]


WORKSPACES = (
    Workspace(
        slug="northwind",
        name="Northwind Logistics GmbH",
        owner_email="owner@northwind.example",
        customers=(
            ("Anja Berger", "anja@berger-handel.example", "Berger Handel"),
            ("Tobias Klein", "tobias@kleinbau.example", "Klein Bau AG"),
            ("Marie Fischer", "marie@fischer-optik.example", "Fischer Optik"),
        ),
    ),
    Workspace(
        slug="acme",
        name="Acme Instruments Ltd",
        owner_email="owner@acme.example",
        customers=(
            ("Priya Raman", "priya@ramanlabs.example", "Raman Labs"),
            ("Lukas Novak", "lukas@novak-eng.example", "Novak Engineering"),
        ),
    ),
)


async def _seed_workspace(workspace: Workspace) -> None:
    async with system_session() as system:
        provisioning = ProvisioningService(system)
        try:
            provisioned = await provisioning.register(
                TenantRegistration(
                    tenant_name=workspace.name,
                    tenant_slug=workspace.slug,
                    email=workspace.owner_email,
                    full_name="Workspace Owner",
                    password=DEMO_PASSWORD,
                )
            )
            tenant_id = provisioned.tenant_id
        except Exception:
            tenant = await provisioning.resolve_slug(workspace.slug)
            tenant_id = tenant.id
            print(f"  {workspace.slug}: already provisioned, topping up")

    # Everything from here runs under the application role with the tenant
    # bound, exactly as a request would -- so the seed exercises the same
    # policies the API does.
    async with app_session(tenant_id) as session:
        roles = RoleRepository(session)
        member_role = await roles.get_by_name(SystemRole.MEMBER.value)
        viewer_role = await roles.get_by_name(SystemRole.VIEWER.value)
        assert member_role is not None and viewer_role is not None

        await _ensure_user(
            session, tenant_id, f"member@{workspace.slug}.example", "Team Member", member_role
        )
        await _ensure_user(
            session, tenant_id, f"viewer@{workspace.slug}.example", "Read Only", viewer_role
        )

        rng = random.Random(workspace.slug)  # noqa: S311 - fixture data, not crypto
        for index, (name, email, company) in enumerate(workspace.customers, start=1):
            customer = Customer(tenant_id=tenant_id, name=name, email=email, company=company)
            session.add(customer)
            await session.flush()

            order = Order(
                tenant_id=tenant_id,
                customer_id=customer.id,
                reference=f"ORD-{index:05d}",
                status=rng.choice([OrderStatus.DRAFT.value, OrderStatus.PLACED.value]),
                currency="EUR",
            )
            order.items = [
                OrderItem(
                    tenant_id=tenant_id,
                    description=description,
                    quantity=rng.randint(1, 12),
                    unit_price_cents=rng.randrange(1500, 90000, 50),
                )
                for description in ("Consulting hours", "Hardware bundle")
            ]
            order.total_cents = sum(i.quantity * i.unit_price_cents for i in order.items)
            session.add(order)
        await session.flush()

    print(f"  {workspace.slug}: ready")


async def _ensure_user(
    session: AsyncSession, tenant_id: UUID, email: str, full_name: str, role: Role
) -> None:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return
    user = User(
        tenant_id=tenant_id,
        email=email,
        full_name=full_name,
        password_hash=hash_password(DEMO_PASSWORD),
    )
    session.add(user)
    await session.flush()
    session.add(UserRole(tenant_id=tenant_id, user_id=user.id, role_id=role.id))
    await session.flush()


async def main() -> None:
    configure_logging("WARNING", "console")
    print("Seeding demo workspaces...")
    for workspace in WORKSPACES:
        await _seed_workspace(workspace)
    await dispose_engines()
    print()
    print("Sign in with any of:")
    for workspace in WORKSPACES:
        for prefix in ("owner", "member", "viewer"):
            email = (
                workspace.owner_email if prefix == "owner" else f"{prefix}@{workspace.slug}.example"
            )
            print(f"  tenant_slug={workspace.slug:<10} email={email:<32} password={DEMO_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
