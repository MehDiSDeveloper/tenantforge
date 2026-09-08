from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import SessionDep, require
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.principal import Principal
from app.schemas.audit import AuditLogRead
from app.services.audit import AuditService

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=Page[AuditLogRead], summary="Read your workspace audit trail")
async def list_audit_logs(
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.AUDIT_READ))],
    params: Annotated[PageParams, Depends(page_params)],
    action: Annotated[str | None, Query(max_length=64)] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[AuditLogRead]:
    """The trail is a tenant-scoped table like any other, so this endpoint
    needs no filter of its own to keep one workspace out of another."""
    return await AuditService(session).search(params, action=action, resource_type=resource_type)
