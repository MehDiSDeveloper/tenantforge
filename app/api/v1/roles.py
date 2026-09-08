from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import SessionDep, require
from app.core.permissions import ALL_PERMISSIONS, Permission
from app.core.principal import Principal
from app.schemas.common import Message
from app.schemas.rbac import PermissionCatalogue, RoleCreate, RoleRead, RoleUpdate
from app.services.rbac import RoleService

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("/permissions", response_model=PermissionCatalogue, summary="Grantable permissions")
async def list_permissions(
    _: Annotated[Principal, Depends(require(Permission.ROLE_READ))],
) -> PermissionCatalogue:
    """Rendered from the enum, so the catalogue cannot drift from the checks."""
    return PermissionCatalogue(permissions=sorted(p.value for p in ALL_PERMISSIONS))


@router.get("", response_model=list[RoleRead], summary="List roles")
async def list_roles(
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.ROLE_READ))],
) -> list[RoleRead]:
    return await RoleService(session).list()


@router.post(
    "", response_model=RoleRead, status_code=status.HTTP_201_CREATED, summary="Create a role"
)
async def create_role(
    payload: RoleCreate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.ROLE_MANAGE))],
) -> RoleRead:
    return await RoleService(session).create(principal, payload)


@router.get("/{role_id}", response_model=RoleRead, summary="Read one role")
async def read_role(
    role_id: UUID,
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.ROLE_READ))],
) -> RoleRead:
    return await RoleService(session).get(role_id)


@router.patch("/{role_id}", response_model=RoleRead, summary="Update a role")
async def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.ROLE_MANAGE))],
) -> RoleRead:
    return await RoleService(session).update(principal, role_id, payload)


@router.delete("/{role_id}", response_model=Message, summary="Delete a role")
async def delete_role(
    role_id: UUID,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.ROLE_MANAGE))],
) -> Message:
    await RoleService(session).delete(principal, role_id)
    return Message(detail="Role deleted.")
