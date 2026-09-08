"""The demonstration domain: customers and the orders they place.

Small on purpose. It exists to show the pattern end to end -- schema, RLS
policy, repository, service, router, tests -- not to be a CRM.
"""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrderStatus(StrEnum):
    DRAFT = "draft"
    PLACED = "placed"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_customers_tenant_id_email"),
        Index("ix_customers_tenant_name", "tenant_id", "name"),
    )
    __tenant_scoped__ = True

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200), default=None)
    notes: Mapped[str | None] = mapped_column(String(2000), default=None)

    orders: Mapped[list[Order]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", lazy="raise"
    )


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference", name="uq_orders_tenant_id_reference"),
        Index("ix_orders_tenant_status", "tenant_id", "status"),
        CheckConstraint("total_cents >= 0", name="total_non_negative"),
    )
    __tenant_scoped__ = True

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=OrderStatus.DRAFT)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")

    # Money is integer minor units. A float total is a rounding bug waiting for
    # a big enough invoice.
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    placed_on: Mapped[date | None] = mapped_column(Date, default=None)

    customer: Mapped[Customer] = relationship(back_populates="orders", lazy="raise")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="OrderItem.created_at",
    )


class OrderItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_non_negative"),
    )
    __tenant_scoped__ = True

    # Denormalised from the parent order so the policy on this table is the
    # same single predicate as every other table. A child row that could only
    # be secured through a join is a child row somebody will eventually read
    # without the join.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    order: Mapped[Order] = relationship(back_populates="items", lazy="raise")

    @property
    def line_total_cents(self) -> int:
        return self.quantity * self.unit_price_cents
