"""The demonstration domain, and the rules that are not CRUD."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import API, Workspace, make_customer


async def test_order_totals_are_computed_server_side(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a, "Ada")
    created = await client.post(
        f"{API}/orders",
        headers=tenant_a.auth,
        json={
            "customer_id": customer_id,
            "items": [
                {"description": "Widget", "quantity": 3, "unit_price_cents": 1000},
                {"description": "Gadget", "quantity": 1, "unit_price_cents": 2500},
            ],
        },
    )
    assert created.status_code == 201
    assert created.json()["total_cents"] == 5500
    assert created.json()["reference"] == "ORD-00001"


async def test_order_references_restart_per_tenant(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    """Counted under the policy, so two workspaces both get ORD-00001 without
    either being able to observe the other numbering."""
    for workspace in (tenant_a, tenant_b):
        customer_id = await make_customer(client, workspace, "Ada")
        order = await client.post(
            f"{API}/orders",
            headers=workspace.auth,
            json={
                "customer_id": customer_id,
                "items": [{"description": "W", "quantity": 1, "unit_price_cents": 100}],
            },
        )
        assert order.json()["reference"] == "ORD-00001"


@pytest.mark.parametrize(
    ("target", "expected"),
    [("placed", 200), ("fulfilled", 422), ("cancelled", 200)],
)
async def test_status_transitions_are_constrained(
    client: AsyncClient, tenant_a: Workspace, target: str, expected: int
) -> None:
    customer_id = await make_customer(client, tenant_a, "Ada")
    order = await client.post(
        f"{API}/orders",
        headers=tenant_a.auth,
        json={
            "customer_id": customer_id,
            "items": [{"description": "W", "quantity": 1, "unit_price_cents": 100}],
        },
    )
    order_id = order.json()["id"]

    response = await client.patch(
        f"{API}/orders/{order_id}/status", headers=tenant_a.auth, json={"status": target}
    )
    assert response.status_code == expected


async def test_duplicate_customer_email_within_a_tenant_conflicts(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    await make_customer(client, tenant_a, "Ada")
    duplicate = await client.post(
        f"{API}/customers",
        headers=tenant_a.auth,
        json={"name": "Ada Again", "email": f"ada@{tenant_a.slug}.example"},
    )
    assert duplicate.status_code == 409


async def test_the_same_email_may_exist_in_two_tenants(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    """Uniqueness is per tenant. A shared address space would be a small,
    permanent leak of who else uses the product."""
    for workspace in (tenant_a, tenant_b):
        response = await client.post(
            f"{API}/customers",
            headers=workspace.auth,
            json={"name": "Ada", "email": "ada@shared.example"},
        )
        assert response.status_code == 201


async def test_pagination_reports_the_tenant_own_total(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    for index in range(5):
        await make_customer(client, tenant_a, f"Person{index}")

    page = (await client.get(f"{API}/customers?limit=2&offset=0", headers=tenant_a.auth)).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2


async def test_health_endpoint_needs_no_token(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
