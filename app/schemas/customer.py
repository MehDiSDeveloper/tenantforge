from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class CustomerRead(ORMModel):
    id: UUID
    tenant_id: UUID
    name: str
    email: EmailStr
    company: str | None
    notes: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    company: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    company: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
