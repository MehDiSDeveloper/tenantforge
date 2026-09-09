"""Dependency wiring.

The chain is short and each link does one thing:

    HTTP request
      -> get_session          an application-role session (RLS applies)
      -> get_principal        decode the token, pin the session to its tenant,
                              load the user and its permissions
      -> require(...)         assert the permissions this endpoint needs

Nothing below this module knows what a request is: services receive a session
and a :class:`Principal`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AppSessionFactory, set_tenant_context
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.logging import tenant_id_var, user_id_var
from app.core.permissions import Permission
from app.core.principal import Principal
from app.core.security import decode_access_token
from app.repositories.user import UserRepository
from app.services.rbac import permissions_for

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


async def get_session() -> AsyncIterator[AsyncSession]:
    """A request-scoped session with *no* tenant bound yet.

    Until something calls :func:`set_tenant_context`, every policy evaluates
    against a NULL tenant and no tenant-scoped row is visible. The default is
    "see nothing", which is the only safe default for this dependency to have.
    """
    async with AppSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_principal(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Authentication required.")

    claims = decode_access_token(credentials.credentials)

    # Bind the session before the first query. Everything the rest of this
    # request reads is filtered by the database from here on.
    await set_tenant_context(session, claims.tenant_id)

    user = await UserRepository(session).get_with_roles(claims.user_id)
    if user is None:
        # Either the user is gone, or the token names a tenant this session is
        # not bound to -- indistinguishable on purpose.
        raise AuthenticationError("Session is no longer valid.")
    if not user.is_active:
        raise AuthenticationError("This account has been deactivated.")
    if user.token_version != claims.token_version:
        raise AuthenticationError("Session has been revoked. Please sign in again.")

    tenant_id_var.set(str(user.tenant_id))
    user_id_var.set(str(user.id))
    request.state.tenant_id = user.tenant_id

    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        full_name=user.full_name,
        roles=frozenset(role.name for role in user.roles),
        permissions=permissions_for(user),
    )


PrincipalDep = Annotated[Principal, Depends(get_principal)]


class require:
    """``dependencies=[Depends(require(Permission.ORDER_WRITE))]``.

    Every permission listed must be held; there is no "any of" variant,
    because an endpoint that accepts either of two permissions is two
    endpoints wearing one URL.
    """

    def __init__(self, *permissions: Permission) -> None:
        if not permissions:
            raise ValueError("require() needs at least one permission")
        self.permissions = permissions

    async def __call__(self, principal: PrincipalDep) -> Principal:
        missing = [p.value for p in self.permissions if not principal.has(p)]
        if missing:
            raise PermissionDeniedError(
                "You do not have permission to perform this action.",
                extra={"missing_permissions": missing},
            )
        return principal


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None
