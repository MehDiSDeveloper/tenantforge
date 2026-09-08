"""Authentication endpoints.

Thin by construction: resolve the tenant, hand the body to a service, return
what it gives back. The only logic here is HTTP -- status codes and headers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import PrincipalDep, SessionDep, client_ip, request_id
from app.core.config import get_settings
from app.core.db import system_session
from app.core.rate_limit import RateLimit
from app.models.tenant import Tenant
from app.schemas.auth import (
    CurrentUser,
    LoginRequest,
    PasswordChange,
    RefreshRequest,
    TenantRegistration,
    TokenPair,
)
from app.schemas.common import Message
from app.schemas.tenant import TenantRead
from app.services.auth import AuthService
from app.services.provisioning import ProvisioningService

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post(
    "/register",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace and its first user",
)
async def register(
    payload: TenantRegistration,
    _: Annotated[None, Depends(RateLimit(settings.register_rate_limit, scope="register"))],
) -> TenantRead:
    """The only endpoint that runs outside a tenant context, because it is the
    one that creates the context. See ``app/services/provisioning.py``."""
    async with system_session() as session:
        provisioned = await ProvisioningService(session).register(payload)
        tenant = await session.get_one(Tenant, provisioned.tenant_id)
        return TenantRead.model_validate(tenant)


@router.post("/login", response_model=TokenPair, summary="Exchange credentials for tokens")
async def login(
    request: Request,
    payload: LoginRequest,
    session: SessionDep,
    _: Annotated[None, Depends(RateLimit(settings.login_rate_limit, scope="login"))],
) -> TokenPair:
    async with system_session() as system:
        tenant = await ProvisioningService(system).resolve_slug(payload.tenant_slug)
    return await AuthService(session).login(
        payload,
        tenant_id=tenant.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=request_id(request),
    )


@router.post("/refresh", response_model=TokenPair, summary="Rotate a refresh token")
async def refresh(
    request: Request,
    payload: RefreshRequest,
    session: SessionDep,
    _: Annotated[None, Depends(RateLimit(settings.refresh_rate_limit, scope="refresh"))],
) -> TokenPair:
    async with system_session() as system:
        tenant = await ProvisioningService(system).resolve_slug(payload.tenant_slug)
    return await AuthService(session).refresh(
        payload.refresh_token,
        tenant_id=tenant.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/logout", response_model=Message, summary="Revoke one session")
async def logout(payload: RefreshRequest, session: SessionDep) -> Message:
    async with system_session() as system:
        tenant = await ProvisioningService(system).resolve_slug(payload.tenant_slug)
    await AuthService(session).logout(payload.refresh_token, tenant_id=tenant.id)
    return Message(detail="Signed out.")


@router.post("/logout-all", response_model=Message, summary="Revoke every session")
async def logout_all(principal: PrincipalDep, session: SessionDep) -> Message:
    revoked = await AuthService(session).logout_everywhere(principal)
    return Message(detail=f"Revoked {revoked} session(s).")


@router.post("/password", response_model=Message, summary="Change your own password")
async def change_password(
    payload: PasswordChange, principal: PrincipalDep, session: SessionDep
) -> Message:
    await AuthService(session).change_password(principal, payload)
    return Message(detail="Password changed. Other sessions have been signed out.")


@router.get("/me", response_model=CurrentUser, summary="The caller identity and permissions")
async def me(principal: PrincipalDep) -> CurrentUser:
    return CurrentUser(
        id=principal.user_id,
        tenant_id=principal.tenant_id,
        email=principal.email,
        full_name=principal.full_name,
        is_active=True,
        roles=sorted(principal.roles),
        permissions=sorted(p.value for p in principal.permissions),
    )
