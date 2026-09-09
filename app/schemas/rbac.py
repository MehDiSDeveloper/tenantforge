from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.permissions import Permission
from app.schemas.common import ORMModel


class RoleRead(ORMModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    is_system: bool
    created_at: datetime
    permissions: list[str] = Field(default_factory=list)

    @field_validator("permissions", mode="before")
    @classmethod
    def _permission_codes(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return sorted(item if isinstance(item, str) else str(item.permission) for item in value)
        return value


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    description: str = Field(default="", max_length=255)
    permissions: list[Permission] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    permissions: list[Permission] | None = None


class PermissionCatalogue(BaseModel):
    """What the API is willing to grant. Rendered from the enum, so the docs
    cannot drift from the checks."""

    permissions: list[str]
