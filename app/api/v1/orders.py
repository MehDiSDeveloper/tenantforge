from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import IfMatchDep, SessionDep, require
from app.core.concurrency import etag
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.principal import Principal
from app.models.domain import OrderStatus
from app.schemas.common import Message
from app.schemas.order import OrderCreate, OrderRead, OrderStatusUpdate
from app.services.order import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

STALE: dict[int | str, dict[str, Any]] = {
    412: {"description": "If-Match named a version that is no longer current."}
}


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
    response: Response,
    principal: Annotated[Principal, Depends(require(Permission.ORDER_WRITE))],
) -> OrderRead:
    order = await OrderService(session).create(principal, payload)
    response.headers["ETag"] = etag(order.version)
    return order


@router.get("/{order_id}", response_model=OrderRead, summary="Read one order")
async def read_order(
    order_id: UUID,
    session: SessionDep,
    response: Response,
    _: Annotated[Principal, Depends(require(Permission.ORDER_READ))],
) -> OrderRead:
    order = await OrderService(session).get(order_id)
    response.headers["ETag"] = etag(order.version)
    return order


@router.patch(
    "/{order_id}/status",
    response_model=OrderRead,
    summary="Move an order along",
    responses=STALE,
)
async def set_order_status(
    order_id: UUID,
    payload: OrderStatusUpdate,
    session: SessionDep,
    response: Response,
    expected: IfMatchDep,
    principal: Annotated[Principal, Depends(require(Permission.ORDER_WRITE))],
) -> OrderRead:
    order = await OrderService(session).set_status(
        principal, order_id, payload.status, expected_versions=expected
    )
    response.headers["ETag"] = etag(order.version)
    return order


@router.delete("/{order_id}", response_model=Message, summary="Delete an order", responses=STALE)
async def delete_order(
    order_id: UUID,
    session: SessionDep,
    expected: IfMatchDep,
    principal: Annotated[Principal, Depends(require(Permission.ORDER_DELETE))],
) -> Message:
    await OrderService(session).delete(principal, order_id, expected_versions=expected)
    return Message(detail="Order deleted.")
