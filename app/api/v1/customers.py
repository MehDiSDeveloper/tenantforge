from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import SessionDep, require
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.principal import Principal
from app.schemas.common import Message
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from app.services.customer import CustomerService

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=Page[CustomerRead], summary="List customers")
async def list_customers(
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.CUSTOMER_READ))],
    params: Annotated[PageParams, Depends(page_params)],
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[CustomerRead]:
    return await CustomerService(session).list(params, q)


@router.post(
    "",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer",
)
async def create_customer(
    payload: CustomerCreate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.CUSTOMER_WRITE))],
) -> CustomerRead:
    return await CustomerService(session).create(principal, payload)


@router.get(
    "/{customer_id}",
    response_model=CustomerRead,
    summary="Read one customer",
    responses={404: {"description": "No such customer in your workspace."}},
)
async def read_customer(
    customer_id: UUID,
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.CUSTOMER_READ))],
) -> CustomerRead:
    return await CustomerService(session).get(customer_id)


@router.patch("/{customer_id}", response_model=CustomerRead, summary="Update a customer")
async def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.CUSTOMER_WRITE))],
) -> CustomerRead:
    return await CustomerService(session).update(principal, customer_id, payload)


@router.delete("/{customer_id}", response_model=Message, summary="Delete a customer")
async def delete_customer(
    customer_id: UUID,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.CUSTOMER_DELETE))],
) -> Message:
    await CustomerService(session).delete(principal, customer_id)
    return Message(detail="Customer deleted.")
