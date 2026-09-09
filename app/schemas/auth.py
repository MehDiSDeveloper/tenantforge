from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel

PASSWORD_MIN_LENGTH = 12


def validate_password(value: str) -> str:
    """Length first, composition second.

    Twelve characters with no class requirements beats eight with four classes;
    the rules that force ``Password1!`` optimise for the checker, not for
    entropy. The one composition rule kept is "not all one character".
    """
    if len(set(value)) < 4:
        raise ValueError("Password is too repetitive.")
    return value


class TenantRegistration(BaseModel):
    """Self-serve signup: creates the tenant, its system roles and its owner."""

    tenant_name: str = Field(min_length=2, max_length=200)
    tenant_slug: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)

    _check_password = field_validator("password")(validate_password)


class LoginRequest(BaseModel):
    """The tenant slug is part of the credential.

    Without it, login would have to search every tenant for an email -- which
    both requires escaping the isolation boundary and turns the login form into
    a cross-tenant account-existence oracle.
    """

    tenant_slug: str = Field(min_length=2, max_length=63)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """The slug travels with the token for the same reason it travels with the
    password: the tenant has to be known before a session can be bound to one."""

    tenant_slug: str = Field(min_length=2, max_length=63)
    refresh_token: str = Field(min_length=16, max_length=512)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - the RFC 6750 scheme name
    expires_in: int = Field(description="Access token lifetime in seconds.")


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)

    _check_password = field_validator("new_password")(validate_password)


class CurrentUser(ORMModel):
    id: UUID
    tenant_id: UUID
    email: EmailStr
    full_name: str
    is_active: bool
    roles: list[str]
    permissions: list[str]
