from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.auth import PASSWORD_MIN_LENGTH, validate_password
from app.schemas.common import ORMModel


class UserRead(ORMModel):
    id: UUID
    tenant_id: UUID
    email: EmailStr
    full_name: str
    is_active: bool
    created_at: datetime
    roles: list[str] = Field(default_factory=list)


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)
    role_names: list[str] = Field(default_factory=list, max_length=20)

    _check_password = field_validator("password")(validate_password)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class UserRoleAssignment(BaseModel):
    role_names: list[str] = Field(max_length=20)
