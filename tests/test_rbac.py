"""Roles and permissions: what a member may do, and what they may not."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import API, DEFAULT_PASSWORD, Workspace, make_customer


async def _member_token(client: AsyncClient, workspace: Workspace, role: str) -> dict[str, str]:
    email = f"{role}@{workspace.slug}.example"
    created = await client.post(
        f"{API}/users",
        headers=workspace.auth,
        json={
            "email": email,
            "full_name": role.title(),
            "password": DEFAULT_PASSWORD,
            "role_names": [role],
        },
    )
    assert created.status_code == 201, created.text
    tokens = await client.post(
        f"{API}/auth/login",
        json={"tenant_slug": workspace.slug, "email": email, "password": DEFAULT_PASSWORD},
    )
    assert tokens.status_code == 200, tokens.text
    return {"Authorization": f"Bearer {tokens.json()['access_token']}"}


async def test_system_roles_are_seeded_for_every_new_tenant(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    roles = (await client.get(f"{API}/roles", headers=tenant_a.auth)).json()
    assert {role["name"] for role in roles} == {"owner", "admin", "member", "viewer"}
    assert all(role["is_system"] for role in roles)


async def test_viewer_can_read_but_not_write(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    auth = await _member_token(client, tenant_a, "viewer")

    assert (await client.get(f"{API}/customers", headers=auth)).status_code == 200

    forbidden = await client.post(
        f"{API}/customers", headers=auth, json={"name": "X", "email": "x@example.com"}
    )
    assert forbidden.status_code == 403
    assert "customer:write" in forbidden.json()["meta"]["missing_permissions"]


async def test_member_can_write_data_but_not_manage_people(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    auth = await _member_token(client, tenant_a, "member")

    created = await client.post(
        f"{API}/customers", headers=auth, json={"name": "Ada", "email": "ada@example.com"}
    )
    assert created.status_code == 201

    forbidden = await client.post(
        f"{API}/users",
        headers=auth,
        json={"email": "new@example.com", "full_name": "New", "password": DEFAULT_PASSWORD},
    )
    assert forbidden.status_code == 403


async def test_admin_may_moderate_but_not_rename_the_workspace(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    """Running the workspace and owning it are different jobs."""
    auth = await _member_token(client, tenant_a, "admin")

    assert (await client.get(f"{API}/users", headers=auth)).status_code == 200
    refused = await client.patch(f"{API}/tenant", headers=auth, json={"name": "Renamed"})
    assert refused.status_code == 403


async def test_custom_role_grants_exactly_what_it_lists(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    created = await client.post(
        f"{API}/roles",
        headers=tenant_a.auth,
        json={
            "name": "billing",
            "description": "Reads orders only",
            "permissions": ["order:read"],
        },
    )
    assert created.status_code == 201
    assert created.json()["permissions"] == ["order:read"]

    auth = await _member_token(client, tenant_a, "billing")
    assert (await client.get(f"{API}/orders", headers=auth)).status_code == 200
    assert (await client.get(f"{API}/customers", headers=auth)).status_code == 403


async def test_built_in_roles_cannot_be_edited_or_deleted(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    roles = (await client.get(f"{API}/roles", headers=tenant_a.auth)).json()
    owner = next(role for role in roles if role["name"] == "owner")

    patched = await client.patch(
        f"{API}/roles/{owner['id']}", headers=tenant_a.auth, json={"description": "nope"}
    )
    assert patched.status_code == 422
    deleted = await client.delete(f"{API}/roles/{owner['id']}", headers=tenant_a.auth)
    assert deleted.status_code == 422


async def test_deactivating_a_user_ends_their_session(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    auth = await _member_token(client, tenant_a, "member")
    users = (await client.get(f"{API}/users", headers=tenant_a.auth)).json()
    member_id = next(u["id"] for u in users["items"] if u["email"].startswith("member@"))

    assert (await client.get(f"{API}/auth/me", headers=auth)).status_code == 200
    await client.patch(
        f"{API}/users/{member_id}", headers=tenant_a.auth, json={"is_active": False}
    )
    assert (await client.get(f"{API}/auth/me", headers=auth)).status_code == 401


async def test_owner_cannot_lock_themselves_out(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    me = (await client.get(f"{API}/auth/me", headers=tenant_a.auth)).json()
    response = await client.patch(
        f"{API}/users/{me['id']}", headers=tenant_a.auth, json={"is_active": False}
    )
    assert response.status_code == 422


async def test_role_change_takes_effect_immediately(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    auth = await _member_token(client, tenant_a, "viewer")
    await make_customer(client, tenant_a, "Ada")

    users = (await client.get(f"{API}/users", headers=tenant_a.auth)).json()
    viewer_id = next(u["id"] for u in users["items"] if u["email"].startswith("viewer@"))

    promoted = await client.put(
        f"{API}/users/{viewer_id}/roles", headers=tenant_a.auth, json={"role_names": ["member"]}
    )
    assert promoted.status_code == 200
    # The old access token is invalidated by the role change, so the client has
    # to sign in again -- permissions never go stale in a live token.
    assert (await client.get(f"{API}/auth/me", headers=auth)).status_code == 401
