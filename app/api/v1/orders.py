from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import SessionDep, require
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.principal import Principal
from app.models.domain import OrderStatus
from app.schemas.common import Message
from app.schemas.order import OrderCreate, OrderRead, OrderStatusUpdate
from app.services.order import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=Page[OrderRead], summary="List orders")
async def list_orders(
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.ORDER_READ))],
    params: Annotated[PageParams, Depends(page_params)],
    customer_id: Annotated[UUID | None, Query()] = None,
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
) -> Page[OrderRead]:
    return await OrderService(session).list(params, customer_id=customer_id, status=order_status)


@router.post(
    "", response_model=OrderRead, status_code=status.HTTP_201_CREATED, summary="Place an order"
)
async def create_order(
    payload: OrderCreate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.ORDER_WRITE))],
) -> OrderRead:
    return await OrderService(session).create(principal, payload)


@router.get("/{order_id}", response_model=OrderRead, summary="Read one order")
async def read_order(
    order_id: UUID,
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.ORDER_READ))],
) -> OrderRead:
    return await OrderService(session).get(order_id)


@router.patch("/{order_id}/status", response_model=OrderRead, summary="Move an order along")
async def set_order_status(
    order_id: UUID,
    payload: OrderStatusUpdate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.ORDER_WRITE))],
) -> OrderRead:
    return await OrderService(session).set_status(principal, order_id, payload.status)


@router.delete("/{order_id}", response_model=Message, summary="Delete an order")
async def delete_order(
    order_id: UUID,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.ORDER_DELETE))],
) -> Message:
    await OrderService(session).delete(principal, order_id)
    return Message(detail="Order deleted.")
