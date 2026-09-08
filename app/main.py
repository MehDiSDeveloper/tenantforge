"""Application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.db import check_database, dispose_engines
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger, log_event
from app.core.middleware import RequestContextMiddleware

settings = get_settings()
logger = get_logger(__name__)

DESCRIPTION = """
A multi-tenant B2B backend starter.

**Tenancy.** Every request is bound to exactly one workspace by the access
token it carries. Isolation is enforced by PostgreSQL row-level security, not
by application filtering, so an identifier belonging to another workspace
resolves to nothing and the API answers `404`.

**Authentication.** `POST /api/v1/auth/register` creates a workspace and its
owner. `POST /api/v1/auth/login` returns a short-lived access token and a
rotating refresh token. Send the access token as `Authorization: Bearer <...>`.

**Authorisation.** Roles are per-workspace rows; permissions are a fixed
vocabulary. `GET /api/v1/auth/me` tells you which ones you hold.
"""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level, settings.log_format)
    await check_database()
    log_event(
        logger,
        "app.started",
        environment=settings.environment,
        version=app.version,
    )
    yield
    await dispose_engines()


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=DESCRIPTION,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
        contact={"name": "TenantForge", "url": "https://github.com/"},
        license_info={"name": "MIT"},
    )

    application.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["x-request-id"],
        )

    register_exception_handlers(application)
    application.include_router(api_router, prefix=settings.api_prefix)

    @application.get("/health", tags=["meta"], summary="Liveness and database reachability")
    async def health() -> dict[str, Any]:
        await check_database()
        return {"status": "ok", "environment": settings.environment}

    return application


app = create_app()
