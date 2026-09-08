from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from app.models.domain import OrderStatus
from app.schemas.common import ORMModel


class OrderItemRead(ORMModel):
    id: UUID
    description: str
    quantity: int
    unit_price_cents: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def line_total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


class OrderItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    quantity: int = Field(gt=0, le=100_000)
    unit_price_cents: int = Field(ge=0, le=1_000_000_000)


class OrderRead(ORMModel):
    id: UUID
    tenant_id: UUID
    customer_id: UUID
    reference: str
    status: OrderStatus
    currency: str
    total_cents: int
    placed_on: date | None
    created_at: datetime
    items: list[OrderItemRead] = Field(default_factory=list)


class OrderCreate(BaseModel):
    customer_id: UUID
    reference: str | None = Field(default=None, max_length=32)
    currency: str = Field(default="EUR", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    items: list[OrderItemCreate] = Field(min_length=1, max_length=200)


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
