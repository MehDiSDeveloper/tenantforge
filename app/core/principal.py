"""The authenticated caller, resolved once per request."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.permissions import Permission


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    tenant_id: UUID
    email: str
    full_name: str
    roles: frozenset[str]
    permissions: frozenset[Permission]

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def has_all(self, permissions: tuple[Permission, ...]) -> bool:
        return all(p in self.permissions for p in permissions)
