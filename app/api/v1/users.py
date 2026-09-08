from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import SessionDep, require
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.principal import Principal
from app.schemas.common import Message
from app.schemas.user import UserCreate, UserRead, UserRoleAssignment, UserUpdate
from app.services.user import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserRead], summary="List members of your workspace")
async def list_users(
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.USER_READ))],
    params: Annotated[PageParams, Depends(page_params)],
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[UserRead]:
    return await UserService(session).list(params, q)


@router.post(
    "", response_model=UserRead, status_code=status.HTTP_201_CREATED, summary="Invite a member"
)
async def create_user(
    payload: UserCreate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.USER_INVITE))],
) -> UserRead:
    return await UserService(session).create(principal, payload)


@router.get("/{user_id}", response_model=UserRead, summary="Read one member")
async def read_user(
    user_id: UUID,
    session: SessionDep,
    _: Annotated[Principal, Depends(require(Permission.USER_READ))],
) -> UserRead:
    return await UserService(session).get(user_id)


@router.patch("/{user_id}", response_model=UserRead, summary="Update one member")
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.USER_UPDATE))],
) -> UserRead:
    return await UserService(session).update(principal, user_id, payload)


@router.put("/{user_id}/roles", response_model=UserRead, summary="Replace a member roles")
async def set_user_roles(
    user_id: UUID,
    payload: UserRoleAssignment,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.ROLE_MANAGE))],
) -> UserRead:
    return await UserService(session).set_roles(principal, user_id, payload.role_names)


@router.delete("/{user_id}", response_model=Message, summary="Remove a member")
async def delete_user(
    user_id: UUID,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require(Permission.USER_DELETE))],
) -> Message:
    await UserService(session).delete(principal, user_id)
    return Message(detail="User removed.")
