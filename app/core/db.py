"""Engines, session factories and the tenant-scoped session.

Two engines exist and the difference between them *is* the isolation model:

``_app_engine``
    Connects as a role with neither ``SUPERUSER`` nor ``BYPASSRLS``. Every
    request-serving session comes from here, so PostgreSQL row-level security
    is applied to it unconditionally. A forgotten ``WHERE tenant_id = ...``
    returns nothing instead of returning somebody else's data.

``_admin_engine``
    The owner role. It exists for migrations and for the three operations that
    happen *before* a tenant context can exist, all of which live behind
    :func:`system_session`:

    1. provisioning a tenant (there is no tenant to scope to yet),
    2. resolving a tenant slug during login/refresh,
    3. seeding.

Nothing else may import ``system_session``; ``tests/test_tenant_isolation.py``
asserts the application role really cannot escape its policies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

#: Session-local GUC the row-level security policies read.
TENANT_GUC: Final = "app.current_tenant"

_settings = get_settings()

_app_engine: AsyncEngine = create_async_engine(
    _settings.async_dsn(),
    echo=_settings.db_echo,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
)

_admin_engine: AsyncEngine = create_async_engine(
    _settings.async_dsn(admin=True),
    echo=_settings.db_echo,
    pool_size=2,
    max_overflow=2,
    pool_pre_ping=True,
)

AppSessionFactory: Final = async_sessionmaker(_app_engine, expire_on_commit=False, autoflush=False)
AdminSessionFactory: Final = async_sessionmaker(
    _admin_engine, expire_on_commit=False, autoflush=False
)


async def set_tenant_context(session: AsyncSession, tenant_id: UUID | None) -> None:
    """Pin ``session``'s current transaction to one tenant.

    ``set_config(..., is_local => true)`` is transaction-scoped, so the value
    cannot outlive the request and leak onto the next checkout of the same
    pooled connection. Passing ``None`` clears it, which makes every policy
    evaluate to ``NULL`` and therefore denies every row -- the isolation fails
    closed, never open.
    """
    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(tenant_id) if tenant_id else ""},
    )


async def current_tenant_context(session: AsyncSession) -> UUID | None:
    raw = await session.scalar(text(f"SELECT nullif(current_setting('{TENANT_GUC}', true), '')"))
    return UUID(raw) if raw else None


@asynccontextmanager
async def app_session(tenant_id: UUID | None = None) -> AsyncIterator[AsyncSession]:
    """A row-level-security-bound session, outside of a request (jobs, tests)."""
    async with AppSessionFactory() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncSession]:
    """The owner-role session. See the module docstring for its three uses."""
    async with AdminSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def dispose_engines() -> None:
    await _app_engine.dispose()
    await _admin_engine.dispose()


async def check_database() -> bool:
    async with _app_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
