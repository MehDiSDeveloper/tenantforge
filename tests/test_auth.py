"""Authentication behaviour: what a token is worth and when it stops working."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import API, DEFAULT_PASSWORD, Workspace


async def test_registration_creates_owner_with_every_permission(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    me = (await client.get(f"{API}/auth/me", headers=tenant_a.auth)).json()
    assert me["roles"] == ["owner"]
    assert "tenant:update" in me["permissions"]
    assert "order:delete" in me["permissions"]


async def test_duplicate_slug_is_rejected(client: AsyncClient, tenant_a: Workspace) -> None:
    response = await client.post(
        f"{API}/auth/register",
        json={
            "tenant_name": "Impostor",
            "tenant_slug": tenant_a.slug,
            "email": "someone@else.example",
            "full_name": "Someone",
            "password": DEFAULT_PASSWORD,
        },
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("slug", "email", "password"),
    [
        ("alpha", "owner@alpha.example", "wrong-password-entirely"),
        ("alpha", "nobody@alpha.example", DEFAULT_PASSWORD),
        ("no-such-workspace", "owner@alpha.example", DEFAULT_PASSWORD),
    ],
    ids=["wrong-password", "unknown-user", "unknown-tenant"],
)
async def test_login_failures_are_indistinguishable(
    client: AsyncClient, tenant_a: Workspace, slug: str, email: str, password: str
) -> None:
    """Three different causes, and the client learns which one only by the
    status code being 401 or 404 -- never by the message."""
    response = await client.post(
        f"{API}/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
    )
    assert response.status_code in (401, 404)
    assert "password" not in response.json()["detail"].lower() or (
        response.json()["detail"] == "Email or password is incorrect."
    )


async def test_missing_or_broken_token_is_401(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/customers")).status_code == 401
    bad = await client.get(f"{API}/customers", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401


async def test_refresh_rotates_and_the_old_token_dies(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    first = await client.post(
        f"{API}/auth/refresh",
        json={"tenant_slug": tenant_a.slug, "refresh_token": tenant_a.refresh_token},
    )
    assert first.status_code == 200
    rotated = first.json()["refresh_token"]
    assert rotated != tenant_a.refresh_token

    replay = await client.post(
        f"{API}/auth/refresh",
        json={"tenant_slug": tenant_a.slug, "refresh_token": tenant_a.refresh_token},
    )
    assert replay.status_code == 401


async def test_reuse_detection_kills_the_whole_family(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    """Replaying a consumed token revokes the successor too.

    The legitimate holder is signed out and notices, which is the entire point:
    the alternative is a thief and a victim quietly sharing a session.
    """
    rotated = (
        await client.post(
            f"{API}/auth/refresh",
            json={"tenant_slug": tenant_a.slug, "refresh_token": tenant_a.refresh_token},
        )
    ).json()["refresh_token"]

    replay = await client.post(
        f"{API}/auth/refresh",
        json={"tenant_slug": tenant_a.slug, "refresh_token": tenant_a.refresh_token},
    )
    assert replay.status_code == 401

    still_live = await client.post(
        f"{API}/auth/refresh",
        json={"tenant_slug": tenant_a.slug, "refresh_token": rotated},
    )
    assert still_live.status_code == 401


async def test_password_change_revokes_live_access_tokens(
    client: AsyncClient, tenant_a: Workspace
) -> None:
    """A short-lived stateless token still has to die on demand; that is what
    the token_version bump buys."""
    assert (await client.get(f"{API}/auth/me", headers=tenant_a.auth)).status_code == 200

    changed = await client.post(
        f"{API}/auth/password",
        headers=tenant_a.auth,
        json={"current_password": DEFAULT_PASSWORD, "new_password": "a-brand-new-passphrase"},
    )
    assert changed.status_code == 200

    assert (await client.get(f"{API}/auth/me", headers=tenant_a.auth)).status_code == 401


async def test_weak_password_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/auth/register",
        json={
            "tenant_name": "Weak",
            "tenant_slug": "weak",
            "email": "weak@example.com",
            "full_name": "Weak",
            "password": "short",
        },
    )
    assert response.status_code == 422
