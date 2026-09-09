"""Test fixtures.

These tests run against a real PostgreSQL, and they have to: the thing under
test is a set of row-level security policies, which SQLite cannot express. A
suite that swapped in SQLite would pass while proving nothing about the
property the whole design rests on.

``docker compose -f docker-compose.test.yml up -d`` gives you one.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

# Defaults must be in place before app.core.config caches its Settings.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("APP_DB_ROLE", "tenantforge_app")
os.environ.setdefault("APP_DB_PASSWORD", "app-test-password")
os.environ.setdefault(
    "DATABASE_ADMIN_URL",
    "postgresql+asyncpg://tenantforge:tenantforge@localhost:5433/tenantforge_test",
)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://tenantforge_app:app-test-password@localhost:5433/tenantforge_test",
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-anywhere-else-0123456789")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import AdminSessionFactory, dispose_engines  # noqa: E402
from app.main import app  # noqa: E402
from app.models import TENANT_SCOPED_TABLES  # noqa: E402

TABLES_TO_CLEAR = ("tenants", *TENANT_SCOPED_TABLES)


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> None:
    """One migration run for the whole session.

    Also the only real test of the migration itself: every policy the suite
    relies on is created here, by the same code that creates it in production.
    """
    config = Config("alembic.ini")
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
async def _clean_database() -> AsyncIterator[None]:
    yield
    async with AdminSessionFactory() as session:
        await session.execute(
            text(f"TRUNCATE {', '.join(TABLES_TO_CLEAR)} RESTART IDENTITY CASCADE")
        )
        await session.commit()


@pytest.fixture(scope="session", autouse=True)
async def _dispose() -> AsyncIterator[None]:
    yield
    await dispose_engines()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@dataclass(frozen=True, slots=True)
class Workspace:
    """A provisioned tenant plus a signed-in owner, built through the public
    API so the fixtures exercise the same code a real client would."""

    slug: str
    tenant_id: str
    owner_email: str
    password: str
    access_token: str
    refresh_token: str

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


DEFAULT_PASSWORD = "seven-league-boots-42"
API = "/api/v1"


async def provision(client: AsyncClient, slug: str) -> Workspace:
    email = f"owner@{slug}.example"
    created = await client.post(
        f"{API}/auth/register",
        json={
            "tenant_name": f"{slug.title()} GmbH",
            "tenant_slug": slug,
            "email": email,
            "full_name": "Owner",
            "password": DEFAULT_PASSWORD,
        },
    )
    assert created.status_code == 201, created.text
    tenant_id = created.json()["id"]

    tokens = await client.post(
        f"{API}/auth/login",
        json={"tenant_slug": slug, "email": email, "password": DEFAULT_PASSWORD},
    )
    assert tokens.status_code == 200, tokens.text
    body = tokens.json()
    return Workspace(
        slug=slug,
        tenant_id=tenant_id,
        owner_email=email,
        password=DEFAULT_PASSWORD,
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
    )


@pytest.fixture
async def tenant_a(client: AsyncClient) -> Workspace:
    return await provision(client, "alpha")


@pytest.fixture
async def tenant_b(client: AsyncClient) -> Workspace:
    return await provision(client, "bravo")


async def make_customer(client: AsyncClient, workspace: Workspace, name: str = "Ada") -> str:
    response = await client.post(
        f"{API}/customers",
        headers=workspace.auth,
        json={"name": name, "email": f"{name.lower()}@{workspace.slug}.example"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def make_order(client: AsyncClient, workspace: Workspace, customer_id: str) -> str:
    response = await client.post(
        f"{API}/orders",
        headers=workspace.auth,
        json={
            "customer_id": customer_id,
            "items": [{"description": "Widget", "quantity": 2, "unit_price_cents": 4999}],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])
