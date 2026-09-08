from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, require
from app.core.permissions import Permission
from app.core.principal import Principal
from app.schemas.tenant import TenantRead, TenantUpdate
from app.services.tenant import TenantService

router = APIRouter(prefix="/tenant", tags=["tenant"])

# Singular and id-less on purpose: a caller can only ever address the tenant
# their token names, so there is no path parameter to tamper with.


@router.get("", response_model=TenantRead, summary="Read your workspace")
async def read_tenant(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.TENANT_READ))],
) -> TenantRead:
    return await TenantService(session).get_current(principal)


@router.patch("", response_model=TenantRead, summary="Rename your workspace")
async def update_tenant(
    payload: TenantUpdate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.TENANT_UPDATE))],
) -> TenantRead:
    return await TenantService(session).update_current(principal, payload)
