"""The headline test: actively try to reach another tenant data, and fail.

Every test here is written from the attacker side. It is not enough that the
happy path returns the right rows -- what has to be true is that a caller
holding a valid token for workspace A, and a correct identifier belonging to
workspace B, gets nothing.

The suite attacks at both levels:

* through the HTTP API, which is where an IDOR would show up, and
* directly against the database with the application role, which is where a
  missing policy would show up even if every router happened to filter
  correctly today.
"""

from __future__ import annotations

import uuid

import pytest
from app.core.db import AdminSessionFactory, AppSessionFactory, set_tenant_context
from app.models import TENANT_SCOPED_TABLES
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import API, Workspace, make_customer, make_order

# --- through the API -------------------------------------------------------


async def test_cross_tenant_customer_read_is_404(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a, "Ada")

    mine = await client.get(f"{API}/customers/{customer_id}", headers=tenant_a.auth)
    assert mine.status_code == 200

    theirs = await client.get(f"{API}/customers/{customer_id}", headers=tenant_b.auth)
    assert theirs.status_code == 404
    # 404 rather than 403: a 403 would confirm the row exists, which is the
    # only question an enumerator is asking.
    assert theirs.json()["code"] == "not_found"


async def test_cross_tenant_customer_update_and_delete_are_404(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a, "Ada")

    patched = await client.patch(
        f"{API}/customers/{customer_id}", headers=tenant_b.auth, json={"name": "Owned"}
    )
    assert patched.status_code == 404

    deleted = await client.delete(f"{API}/customers/{customer_id}", headers=tenant_b.auth)
    assert deleted.status_code == 404

    # And the row is untouched.
    intact = await client.get(f"{API}/customers/{customer_id}", headers=tenant_a.auth)
    assert intact.status_code == 200
    assert intact.json()["name"] == "Ada"


async def test_cross_tenant_order_access_is_404(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a, "Ada")
    order_id = await make_order(client, tenant_a, customer_id)

    assert (await client.get(f"{API}/orders/{order_id}", headers=tenant_b.auth)).status_code == 404
    status_change = await client.patch(
        f"{API}/orders/{order_id}/status", headers=tenant_b.auth, json={"status": "cancelled"}
    )
    assert status_change.status_code == 404


async def test_order_cannot_be_attached_to_another_tenant_customer(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    """The classic mass-assignment shape: a body naming somebody else row."""
    victim_customer = await make_customer(client, tenant_a, "Ada")

    response = await client.post(
        f"{API}/orders",
        headers=tenant_b.auth,
        json={
            "customer_id": victim_customer,
            "items": [{"description": "Theft", "quantity": 1, "unit_price_cents": 1}],
        },
    )
    assert response.status_code == 404


async def test_listings_never_include_another_tenant_rows(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    await make_customer(client, tenant_a, "Ada")
    await make_customer(client, tenant_a, "Grace")
    await make_customer(client, tenant_b, "Katherine")

    a_list = (await client.get(f"{API}/customers", headers=tenant_a.auth)).json()
    b_list = (await client.get(f"{API}/customers", headers=tenant_b.auth)).json()

    assert a_list["total"] == 2
    assert b_list["total"] == 1
    assert {item["tenant_id"] for item in a_list["items"]} == {tenant_a.tenant_id}
    assert {item["tenant_id"] for item in b_list["items"]} == {tenant_b.tenant_id}


async def test_user_directory_is_isolated(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    users = (await client.get(f"{API}/users", headers=tenant_a.auth)).json()
    assert [u["email"] for u in users["items"]] == [tenant_a.owner_email]

    a_owner_id = users["items"][0]["id"]
    assert (await client.get(f"{API}/users/{a_owner_id}", headers=tenant_b.auth)).status_code == 404


async def test_audit_trail_is_isolated(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    await make_customer(client, tenant_a, "Ada")

    a_logs = (await client.get(f"{API}/audit-logs", headers=tenant_a.auth)).json()
    b_logs = (await client.get(f"{API}/audit-logs", headers=tenant_b.auth)).json()

    assert any(entry["action"] == "customer.created" for entry in a_logs["items"])
    assert all(entry["action"] != "customer.created" for entry in b_logs["items"])
    assert {entry["tenant_id"] for entry in b_logs["items"]} == {tenant_b.tenant_id}


async def test_refresh_token_is_useless_against_another_tenant(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    response = await client.post(
        f"{API}/auth/refresh",
        json={"tenant_slug": tenant_b.slug, "refresh_token": tenant_a.refresh_token},
    )
    assert response.status_code == 401


# --- directly against the database ----------------------------------------


async def test_unbound_session_sees_nothing(client: AsyncClient, tenant_a: Workspace) -> None:
    """A session that forgot to set a tenant reads zero rows, not all rows.

    This is the fail-closed property. ``current_setting`` returns NULL, the
    predicate is NULL, and NULL is not true -- so the policy denies rather
    than admits.
    """
    await make_customer(client, tenant_a, "Ada")

    async with AppSessionFactory() as session:
        assert await session.scalar(text("SELECT count(*) FROM customers")) == 0
        assert await session.scalar(text("SELECT count(*) FROM users")) == 0
        assert await session.scalar(text("SELECT count(*) FROM audit_logs")) == 0


async def test_bound_session_sees_only_its_own_tenant(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    await make_customer(client, tenant_a, "Ada")
    await make_customer(client, tenant_b, "Katherine")

    async with AppSessionFactory() as session:
        await set_tenant_context(session, uuid.UUID(tenant_a.tenant_id))
        names = list(await session.scalars(text("SELECT name FROM customers")))
        assert names == ["Ada"]

        # Naming the other tenant explicitly changes nothing: the policy is
        # ANDed with whatever the query asks for.
        hidden = await session.scalar(
            text("SELECT count(*) FROM customers WHERE tenant_id = :other"),
            {"other": tenant_b.tenant_id},
        )
        assert hidden == 0


async def test_insert_stamped_with_another_tenant_is_rejected(
    tenant_a: Workspace, tenant_b: Workspace
) -> None:
    """``WITH CHECK`` half of the policy.

    A service that computed the wrong ``tenant_id`` -- from a request body, say
    -- does not write a leaked row; the database refuses the statement.
    """
    async with AppSessionFactory() as session:
        await set_tenant_context(session, uuid.UUID(tenant_a.tenant_id))
        with pytest.raises(ProgrammingError) as excinfo:
            await session.execute(
                text(
                    "INSERT INTO customers (id, tenant_id, name, email) "
                    "VALUES (:id, :tenant, 'Smuggled', 'smuggled@example.com')"
                ),
                {"id": str(uuid.uuid4()), "tenant": tenant_b.tenant_id},
            )
        assert "row-level security" in str(excinfo.value).lower()
        await session.rollback()


async def test_application_role_cannot_bypass_rls() -> None:
    """The attribute check behind everything above.

    A role with SUPERUSER or BYPASSRLS ignores every policy in the database.
    If this assertion ever fails, none of the other tests in this file mean
    anything -- so it is asserted rather than assumed.
    """
    async with AppSessionFactory() as session:
        row = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False

    async with AppSessionFactory() as session:
        with pytest.raises(ProgrammingError):
            # And it cannot simply turn the policies off, either.
            await session.execute(text("ALTER TABLE customers DISABLE ROW LEVEL SECURITY"))
        await session.rollback()


@pytest.mark.parametrize("table", ("tenants", *TENANT_SCOPED_TABLES))
async def test_every_table_has_forced_rls_and_a_policy(table: str) -> None:
    """A new tenant-scoped table cannot ship without a policy.

    ``TENANT_SCOPED_TABLES`` is the same list the migration iterates, so
    adding a table to the model registry and forgetting the policy fails here
    rather than in production.
    """
    async with AdminSessionFactory() as session:
        flags = (
            await session.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = to_regclass(:name)"
                ),
                {"name": table},
            )
        ).one()
        assert flags.relrowsecurity is True, f"{table} has no row-level security"
        # FORCE matters: without it the table owner is exempt from its own
        # policies, and the owner is who the migrations and the seed run as.
        assert flags.relforcerowsecurity is True, f"{table} does not force RLS on its owner"

        policies = await session.scalar(
            text("SELECT count(*) FROM pg_policies WHERE tablename = :name"), {"name": table}
        )
        assert policies == 1, f"{table} should have exactly one policy"
