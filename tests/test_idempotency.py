"""Idempotency keys: a retried create is answered, not repeated.

Written from the side of a client on a bad network -- the one whose request
timed out and who now has to decide whether to send it again.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

from app.core.db import AdminSessionFactory, set_tenant_context
from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import API, Workspace, make_customer


def _order(customer_id: str, quantity: int = 1) -> dict[str, Any]:
    return {
        "customer_id": customer_id,
        "items": [{"description": "Widget", "quantity": quantity, "unit_price_cents": 4999}],
    }


def _keyed(workspace: Workspace, key: str) -> dict[str, str]:
    return {**workspace.auth, "Idempotency-Key": key}


async def _order_count(client: AsyncClient, workspace: Workspace) -> int:
    response = await client.get(f"{API}/orders", headers=workspace.auth)
    return int(response.json()["total"])


async def test_a_retried_order_is_answered_not_repeated(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a)
    headers = _keyed(tenant_a, str(uuid4()))

    first = await client.post(f"{API}/orders", headers=headers, json=_order(customer_id))
    assert first.status_code == 201, first.text
    assert "idempotent-replayed" not in first.headers

    retry = await client.post(f"{API}/orders", headers=headers, json=_order(customer_id))
    assert retry.status_code == 201
    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.json() == first.json()
    assert retry.headers["etag"] == first.headers["etag"]

    assert await _order_count(client, tenant_a) == 1


async def test_a_retried_customer_is_replayed_rather_than_a_conflict(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    """Without a key the retry would be a 409 on the unique email -- telling a
    client whose first attempt succeeded that it failed."""
    headers = _keyed(tenant_a, "create-ada-1")
    body = {"name": "Ada", "email": "ada@alpha.example"}
    first = await client.post(f"{API}/customers", headers=headers, json=body)
    retry = await client.post(f"{API}/customers", headers=headers, json=body)
    assert first.status_code == retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]


async def test_concurrent_retries_create_exactly_one_order(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a)
    headers = _keyed(tenant_a, str(uuid4()))

    responses = await asyncio.gather(
        *(client.post(f"{API}/orders", headers=headers, json=_order(customer_id)) for _ in range(4))
    )
    assert [r.status_code for r in responses] == [201] * 4, [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    assert await _order_count(client, tenant_a) == 1


async def test_a_key_reused_for_a_different_request_is_refused(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a)
    headers = _keyed(tenant_a, "reused")
    first = await client.post(f"{API}/orders", headers=headers, json=_order(customer_id, 1))
    assert first.status_code == 201

    other = await client.post(f"{API}/orders", headers=headers, json=_order(customer_id, 5))
    assert other.status_code == 422
    assert other.json()["code"] == "idempotency_key_reused"

    # Same payload, different endpoint: still a different request.
    customer = await client.post(
        f"{API}/customers", headers=headers, json={"name": "Bo", "email": "bo@alpha.example"}
    )
    assert customer.status_code == 422
    assert await _order_count(client, tenant_a) == 1


async def test_a_failed_request_does_not_spend_the_key(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    """The claim shares the write's transaction, so a request that rolled back
    leaves nothing behind to replay."""
    headers = _keyed(tenant_a, "try-again")
    missing = await client.post(f"{API}/orders", headers=headers, json=_order(str(uuid4())))
    assert missing.status_code == 404

    customer_id = await make_customer(client, tenant_a)
    placed = await client.post(f"{API}/orders", headers=headers, json=_order(customer_id))
    assert placed.status_code == 201, placed.text
    assert "idempotent-replayed" not in placed.headers


async def test_requests_without_a_key_are_unchanged(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a)
    for _ in range(2):
        response = await client.post(
            f"{API}/orders", headers=tenant_a.auth, json=_order(customer_id)
        )
        assert response.status_code == 201
    assert await _order_count(client, tenant_a) == 2


async def test_a_key_never_replays_across_workspaces(
    client: AsyncClient, tenant_a: Workspace, tenant_b: Workspace
) -> None:
    key = "shared-by-coincidence"
    a_order = await client.post(
        f"{API}/orders",
        headers=_keyed(tenant_a, key),
        json=_order(await make_customer(client, tenant_a)),
    )
    b_order = await client.post(
        f"{API}/orders",
        headers=_keyed(tenant_b, key),
        json=_order(await make_customer(client, tenant_b)),
    )
    assert a_order.status_code == b_order.status_code == 201
    assert "idempotent-replayed" not in b_order.headers
    assert b_order.json()["tenant_id"] == tenant_b.tenant_id
    assert b_order.json()["id"] != a_order.json()["id"]


async def test_an_expired_key_can_be_used_again(client: AsyncClient, tenant_a: Workspace) -> None:
    customer_id = await make_customer(client, tenant_a)
    headers = _keyed(tenant_a, "yesterday")
    assert (
        await client.post(f"{API}/orders", headers=headers, json=_order(customer_id))
    ).status_code == 201

    async with AdminSessionFactory() as session:
        await set_tenant_context(session, UUID(tenant_a.tenant_id))
        await session.execute(
            text("UPDATE idempotency_keys SET created_at = now() - interval '2 days'")
        )
        await session.commit()

    again = await client.post(f"{API}/orders", headers=headers, json=_order(customer_id, 3))
    assert again.status_code == 201, again.text
    assert "idempotent-replayed" not in again.headers
    assert await _order_count(client, tenant_a) == 2


async def test_a_malformed_key_is_rejected(client: AsyncClient, tenant_a: Workspace) -> None:
    customer_id = await make_customer(client, tenant_a)
    response = await client.post(
        f"{API}/orders", headers=_keyed(tenant_a, "x" * 256), json=_order(customer_id)
    )
    assert response.status_code == 422
    assert await _order_count(client, tenant_a) == 0
