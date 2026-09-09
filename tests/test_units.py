"""Pieces that need neither HTTP nor a database.

The layering claim in the README is only worth making if some of the logic can
actually be tested this way.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import AuthenticationError
from app.core.permissions import (
    ALL_PERMISSIONS,
    SYSTEM_ROLE_PERMISSIONS,
    Permission,
    SystemRole,
)
from app.core.principal import Principal
from app.core.rate_limit import InMemoryRateLimiter, Rate
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.domain import OrderStatus
from app.services.order import ALLOWED_TRANSITIONS


def test_password_hash_is_argon2id_and_salted() -> None:
    first, second = hash_password("same-passphrase"), hash_password("same-passphrase")
    assert first.startswith("$argon2id$")
    assert first != second, "identical passwords must not produce identical hashes"
    assert verify_password("same-passphrase", first)
    assert not verify_password("other-passphrase", first)


def test_refresh_token_digest_is_keyed_and_stable() -> None:
    digest = hash_refresh_token("a-token")
    assert digest == hash_refresh_token("a-token")
    assert digest != "a-token"
    assert len(digest) == 64


def test_access_token_round_trips() -> None:
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token, ttl = create_access_token(user_id=user_id, tenant_id=tenant_id, token_version=7)
    claims = decode_access_token(token)
    assert (claims.user_id, claims.tenant_id, claims.token_version) == (user_id, tenant_id, 7)
    assert ttl > 0


def test_tampered_access_token_is_refused() -> None:
    token, _ = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), token_version=1
    )
    head, payload, signature = token.split(".")
    with pytest.raises(AuthenticationError):
        decode_access_token(f"{head}.{payload}x.{signature}")


def test_owner_holds_every_permission_and_viewer_holds_no_write() -> None:
    assert SYSTEM_ROLE_PERMISSIONS[SystemRole.OWNER] == ALL_PERMISSIONS
    viewer = SYSTEM_ROLE_PERMISSIONS[SystemRole.VIEWER]
    assert all(":read" in p.value for p in viewer)


def test_admin_cannot_rename_the_workspace() -> None:
    """Moderating a workspace and owning it are separate powers."""
    assert Permission.TENANT_UPDATE not in SYSTEM_ROLE_PERMISSIONS[SystemRole.ADMIN]
    assert Permission.TENANT_UPDATE in SYSTEM_ROLE_PERMISSIONS[SystemRole.OWNER]


def test_principal_permission_check() -> None:
    principal = Principal(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="a@b.example",
        full_name="A",
        roles=frozenset({"member"}),
        permissions=frozenset({Permission.ORDER_READ}),
    )
    assert principal.has(Permission.ORDER_READ)
    assert not principal.has(Permission.ORDER_WRITE)


def test_terminal_order_states_have_no_exit() -> None:
    assert ALLOWED_TRANSITIONS[OrderStatus.FULFILLED] == frozenset()
    assert ALLOWED_TRANSITIONS[OrderStatus.CANCELLED] == frozenset()


@pytest.mark.parametrize(
    ("spec", "seconds"), [("10/minute", 60), ("5/hour", 3600), ("2/second", 1)]
)
def test_rate_spec_parsing(spec: str, seconds: int) -> None:
    assert Rate.parse(spec).seconds == seconds


async def test_rate_limiter_allows_then_blocks() -> None:
    limiter = InMemoryRateLimiter()
    rate = Rate(times=2, seconds=60)
    assert await limiter.hit("k", rate) == 0
    assert await limiter.hit("k", rate) == 0
    assert await limiter.hit("k", rate) > 0
    # Buckets are per key, so one caller cannot exhaust another allowance.
    assert await limiter.hit("other", rate) == 0
