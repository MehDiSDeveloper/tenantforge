from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import IfMatchDep, SessionDep, require
from app.core.concurrency import etag
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.principal import Principal
from app.schemas.common import Message
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from app.services.customer import CustomerService

router = APIRouter(prefix="/customers", tags=["customers"])

STALE: dict[int | str, dict[str, Any]] = {
    412: {"description": "If-Match named a version that is no longer current."}
}


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
    response: Response,
    principal: Annotated[Principal, Depends(require(Permission.CUSTOMER_WRITE))],
) -> CustomerRead:
    customer = await CustomerService(session).create(principal, payload)
    response.headers["ETag"] = etag(customer.version)
    return customer


@router.get(
    "/{customer_id}",
    response_model=CustomerRead,
    summary="Read one customer",
    responses={404: {"description": "No such customer in your workspace."}},
)
async def read_customer(
    customer_id: UUID,
    session: SessionDep,
    response: Response,
    _: Annotated[Principal, Depends(require(Permission.CUSTOMER_READ))],
) -> CustomerRead:
    customer = await CustomerService(session).get(customer_id)
    response.headers["ETag"] = etag(customer.version)
    return customer


@router.patch(
    "/{customer_id}", response_model=CustomerRead, summary="Update a customer", responses=STALE
)
async def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    session: SessionDep,
    response: Response,
    expected: IfMatchDep,
    principal: Annotated[Principal, Depends(require(Permission.CUSTOMER_WRITE))],
) -> CustomerRead:
    customer = await CustomerService(session).update(
        principal, customer_id, payload, expected_versions=expected
    )
    response.headers["ETag"] = etag(customer.version)
    return customer


@router.delete(
    "/{customer_id}", response_model=Message, summary="Delete a customer", responses=STALE
)
async def delete_customer(
    customer_id: UUID,
    session: SessionDep,
    expected: IfMatchDep,
    principal: Annotated[Principal, Depends(require(Permission.CUSTOMER_DELETE))],
) -> Message:
    await CustomerService(session).delete(principal, customer_id, expected_versions=expected)
    return Message(detail="Customer deleted.")
