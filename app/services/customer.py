from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import Page, PageParams
from app.core.principal import Principal
from app.models.domain import Customer
from app.repositories.customer import CustomerRepository
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from app.services.audit import AuditService


class CustomerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.audit = AuditService(session)

    async def list(self, params: PageParams, query: str | None = None) -> Page[CustomerRead]:
        rows, total = await self.customers.search(params, query)
        return Page.build([CustomerRead.model_validate(r) for r in rows], total, params)

    async def get(self, customer_id: UUID) -> CustomerRead:
        return CustomerRead.model_validate(await self._require(customer_id))

    async def create(self, principal: Principal, payload: CustomerCreate) -> CustomerRead:
        customer = Customer(
            tenant_id=principal.tenant_id,
            name=payload.name.strip(),
            email=payload.email.lower(),
            company=payload.company,
            notes=payload.notes,
        )
        try:
            await self.customers.add(customer)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("A customer with that email already exists.") from exc

        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="customer.created",
            resource_type="customer",
            resource_id=str(customer.id),
            actor=principal,
            changes={"name": customer.name, "email": customer.email},
        )
        return CustomerRead.model_validate(customer)

    async def update(
        self, principal: Principal, customer_id: UUID, payload: CustomerUpdate
    ) -> CustomerRead:
        customer = await self._require(customer_id)
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        for field, value in changes.items():
            setattr(customer, field, value.lower() if field == "email" else value)
        await self.session.flush()
        if changes:
            await self.audit.record(
                tenant_id=principal.tenant_id,
                action="customer.updated",
                resource_type="customer",
                resource_id=str(customer.id),
                actor=principal,
                changes=changes,
            )
        return CustomerRead.model_validate(customer)

    async def delete(self, principal: Principal, customer_id: UUID) -> None:
        customer = await self._require(customer_id)
        name = customer.name
        await self.customers.delete(customer)
        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="customer.deleted",
            resource_type="customer",
            resource_id=str(customer_id),
            actor=principal,
            changes={"name": name},
        )

    async def _require(self, customer_id: UUID) -> Customer:
        """A customer of another tenant is invisible to this session, so this
        raises 404 for both "does not exist" and "is not yours" -- the only
        pair of answers that leaks nothing."""
        customer = await self.customers.get(customer_id)
        if customer is None:
            raise NotFoundError("Customer not found.")
        return customer
