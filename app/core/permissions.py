"""The permission vocabulary and the built-in per-tenant roles.

Permissions are a closed set defined in code; *roles* are rows owned by a
tenant. That split is deliberate: a tenant may invent "Warehouse supervisor"
without a deployment, but it can never invent a capability the code does not
already know how to check.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Permission(StrEnum):
    TENANT_READ = "tenant:read"
    TENANT_UPDATE = "tenant:update"

    USER_READ = "user:read"
    USER_INVITE = "user:invite"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"

    ROLE_READ = "role:read"
    ROLE_MANAGE = "role:manage"

    CUSTOMER_READ = "customer:read"
    CUSTOMER_WRITE = "customer:write"
    CUSTOMER_DELETE = "customer:delete"

    ORDER_READ = "order:read"
    ORDER_WRITE = "order:write"
    ORDER_DELETE = "order:delete"

    AUDIT_READ = "audit:read"


ALL_PERMISSIONS: Final[frozenset[Permission]] = frozenset(Permission)

_READ_ONLY: Final[frozenset[Permission]] = frozenset(
    {
        Permission.TENANT_READ,
        Permission.USER_READ,
        Permission.ROLE_READ,
        Permission.CUSTOMER_READ,
        Permission.ORDER_READ,
    }
)

_MEMBER: Final[frozenset[Permission]] = _READ_ONLY | {
    Permission.CUSTOMER_WRITE,
    Permission.ORDER_WRITE,
}

_ADMIN: Final[frozenset[Permission]] = ALL_PERMISSIONS - {Permission.TENANT_UPDATE}


class SystemRole(StrEnum):
    """Roles seeded into every new tenant. They are ordinary rows, flagged
    ``is_system`` so the API refuses to rename or delete them."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


SYSTEM_ROLE_PERMISSIONS: Final[dict[SystemRole, frozenset[Permission]]] = {
    SystemRole.OWNER: ALL_PERMISSIONS,
    SystemRole.ADMIN: _ADMIN,
    SystemRole.MEMBER: _MEMBER,
    SystemRole.VIEWER: _READ_ONLY,
}

SYSTEM_ROLE_DESCRIPTIONS: Final[dict[SystemRole, str]] = {
    SystemRole.OWNER: "Full control of the tenant, including billing-level settings.",
    SystemRole.ADMIN: "Manages users, roles and all business data.",
    SystemRole.MEMBER: "Reads and writes business data.",
    SystemRole.VIEWER: "Read-only access.",
}
