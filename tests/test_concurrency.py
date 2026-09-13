"""Optimistic concurrency: a stale write is refused, never silently applied.

Written from the side of the second of two editors -- the one whose save would,
without a version check, quietly erase the first one's work.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from app.core.db import AppSessionFactory, set_tenant_context
from app.models.domain import Customer
from httpx import AsyncClient
from sqlalchemy.orm.exc import StaleDataError

from tests.conftest import API, Workspace, make_customer, make_order


async def test_reads_carry_the_version_as_an_etag(client: AsyncClient, tenant_a: Workspace) -> None:
    customer_id = await make_customer(client, tenant_a)
    response = await client.get(f"{API}/customers/{customer_id}", headers=tenant_a.auth)
    assert response.json()["version"] == 1
    assert response.headers["etag"] == '"1"'


async def test_a_write_with_the_current_etag_succeeds_and_moves_the_version(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a)
    response = await client.patch(
        f"{API}/customers/{customer_id}",
        headers={**tenant_a.auth, "If-Match": '"1"'},
        json={"name": "Ada Lovelace"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert response.headers["etag"] == '"2"'


async def test_the_second_of_two_editors_is_refused(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a)
    url = f"{API}/customers/{customer_id}"
    # Both open the record.
    seen_by_first = (await client.get(url, headers=tenant_a.auth)).headers["etag"]
    seen_by_second = (await client.get(url, headers=tenant_a.auth)).headers["etag"]

    first = await client.patch(
        url, headers={**tenant_a.auth, "If-Match": seen_by_first}, json={"name": "First"}
    )
    assert first.status_code == 200

    second = await client.patch(
        url, headers={**tenant_a.auth, "If-Match": seen_by_second}, json={"name": "Second"}
    )
    assert second.status_code == 412
    assert second.json()["code"] == "precondition_failed"
    assert second.json()["meta"]["current_version"] == 2

    # The first editor's work survived.
    assert (await client.get(url, headers=tenant_a.auth)).json()["name"] == "First"


async def test_a_stale_delete_is_refused_and_the_row_survives(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a)
    url = f"{API}/customers/{customer_id}"
    await client.patch(url, headers=tenant_a.auth, json={"notes": "VIP since Tuesday"})

    deleted = await client.delete(url, headers={**tenant_a.auth, "If-Match": '"1"'})
    assert deleted.status_code == 412
    assert (await client.get(url, headers=tenant_a.auth)).status_code == 200


async def test_a_stale_order_status_change_is_refused(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    order_id = await make_order(client, tenant_a, await make_customer(client, tenant_a))
    url = f"{API}/orders/{order_id}"
    placed = await client.patch(
        f"{url}/status", headers={**tenant_a.auth, "If-Match": '"1"'}, json={"status": "placed"}
    )
    assert placed.status_code == 200
    assert placed.headers["etag"] == '"2"'

    # Somebody still looking at the draft tries to cancel it.
    cancelled = await client.patch(
        f"{url}/status",
        headers={**tenant_a.auth, "If-Match": '"1"'},
        json={"status": "cancelled"},
    )
    assert cancelled.status_code == 412
    assert (await client.get(url, headers=tenant_a.auth)).json()["status"] == "placed"


@pytest.mark.parametrize("if_match", [None, "*"])
async def test_writes_without_a_precondition_stay_unconditional(
    client: AsyncClient, tenant_a: Workspace, if_match: str | None
) -> None:
    """Opting in is the client's choice; existing clients are not broken."""
    customer_id = await make_customer(client, tenant_a)
    headers = dict(tenant_a.auth)
    if if_match:
        headers["If-Match"] = if_match
    for name in ("One", "Two"):
        response = await client.patch(
            f"{API}/customers/{customer_id}", headers=headers, json={"name": name}
        )
        assert response.status_code == 200


async def test_an_unrecognisable_etag_fails_rather_than_being_ignored(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    customer_id = await make_customer(client, tenant_a)
    response = await client.patch(
        f"{API}/customers/{customer_id}",
        headers={**tenant_a.auth, "If-Match": '"not-a-version"'},
        json={"name": "Nope"},
    )
    assert response.status_code == 412


async def test_a_write_racing_inside_the_request_is_refused_by_the_database(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    """The ETag covers the gap between a client's read and its write. This is
    the gap *inside* one request -- between our SELECT and our UPDATE -- which
    only the ``WHERE version = ...`` on the UPDATE itself can close."""
    customer_id = UUID(await make_customer(client, tenant_a))
    tenant_id = UUID(tenant_a.tenant_id)

    async with AppSessionFactory() as ours, AppSessionFactory() as theirs:
        for session in (ours, theirs):
            await set_tenant_context(session, tenant_id)
        mine = await ours.get(Customer, customer_id)
        other = await theirs.get(Customer, customer_id)
        assert mine is not None and other is not None

        other.name = "Theirs"
        await theirs.commit()

        mine.name = "Mine"
        with pytest.raises(StaleDataError):
            await ours.flush()
        await ours.rollback()
