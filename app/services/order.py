from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.pagination import Page, PageParams
from app.core.principal import Principal
from app.models.domain import Order, OrderItem, OrderStatus
from app.repositories.customer import CustomerRepository
from app.repositories.order import OrderRepository
from app.schemas.order import OrderCreate, OrderRead
from app.services.audit import AuditService

#: Which transitions are legal. A status column with no transition table is a
#: column that will eventually hold "fulfilled" for a cancelled order.
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.PLACED, OrderStatus.CANCELLED}),
    OrderStatus.PLACED: frozenset({OrderStatus.FULFILLED, OrderStatus.CANCELLED}),
    OrderStatus.FULFILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.customers = CustomerRepository(session)
        self.audit = AuditService(session)

    async def list(
        self,
        params: PageParams,
        *,
        customer_id: UUID | None = None,
        status: OrderStatus | None = None,
    ) -> Page[OrderRead]:
        rows, total = await self.orders.search(params, customer_id=customer_id, status=status)
        return Page.build([OrderRead.model_validate(r) for r in rows], total, params)

    async def get(self, order_id: UUID) -> OrderRead:
        return OrderRead.model_validate(await self._require(order_id))

    async def create(self, principal: Principal, payload: OrderCreate) -> OrderRead:
        # The customer lookup is itself the cross-tenant check: a customer id
        # from another tenant does not resolve under this session policy, so
        # an order can never be attached across the boundary.
        customer = await self.customers.get(payload.customer_id)
        if customer is None:
            raise NotFoundError("Customer not found.")

        reference = payload.reference or await self.orders.next_reference()
        order = Order(
            tenant_id=principal.tenant_id,
            customer_id=customer.id,
            reference=reference,
            status=OrderStatus.DRAFT.value,
            currency=payload.currency,
            total_cents=sum(i.quantity * i.unit_price_cents for i in payload.items),
        )
        order.items = [
            OrderItem(
                tenant_id=principal.tenant_id,
                description=item.description,
                quantity=item.quantity,
                unit_price_cents=item.unit_price_cents,
            )
            for item in payload.items
        ]
        try:
            await self.orders.add(order)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("An order with that reference already exists.") from exc

        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="order.created",
            resource_type="order",
            resource_id=str(order.id),
            actor=principal,
            changes={"reference": order.reference, "total_cents": order.total_cents},
        )
        return OrderRead.model_validate(order)

    async def set_status(
        self, principal: Principal, order_id: UUID, status: OrderStatus
    ) -> OrderRead:
        order = await self._require(order_id)
        current = OrderStatus(order.status)
        if status == current:
            return OrderRead.model_validate(order)
        if status not in ALLOWED_TRANSITIONS[current]:
            raise ValidationError(f"An order cannot go from {current.value} to {status.value}.")

        order.status = status.value
        if status is OrderStatus.PLACED:
            order.placed_on = datetime.now(UTC).date()
        await self.session.flush()

        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="order.status_changed",
            resource_type="order",
            resource_id=str(order.id),
            actor=principal,
            changes={"from": current.value, "to": status.value},
        )
        return OrderRead.model_validate(order)

    async def delete(self, principal: Principal, order_id: UUID) -> None:
        order = await self._require(order_id)
        reference = order.reference
        await self.orders.delete(order)
        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="order.deleted",
            resource_type="order",
            resource_id=str(order_id),
            actor=principal,
            changes={"reference": reference},
        )

    async def _require(self, order_id: UUID) -> Order:
        order = await self.orders.get_with_items(order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        return order
