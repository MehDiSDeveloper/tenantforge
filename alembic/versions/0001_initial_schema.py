"""Initial schema, application role and row-level security policies.

This single migration is the isolation model. Three things happen in it and
none of them is optional:

1. every tenant-scoped table gets a ``tenant_id``;
2. a login role is provisioned that has neither SUPERUSER nor BYPASSRLS -- the
   role the application connects as, and the reason a policy cannot be
   sidestepped by the code that runs above it;
3. every tenant-scoped table gets ``ENABLE`` *and* ``FORCE`` row level
   security plus one policy. ``FORCE`` matters: without it a table owner is
   exempt from its own policies, which is precisely the case a
   locally-run-as-superuser test would fail to notice.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings
from app.models import TENANT_SCOPED_TABLES

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

settings = get_settings()

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    ]


def _tenant_fk() -> sa.Column[postgresql.UUID]:
    return sa.Column(
        "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def _create_tables() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"])

    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.create_table(
        "roles",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_id_name"),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])

    op.create_table(
        "role_permissions",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("role_id", UUID, sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "role_id", "permission", name="uq_role_permissions_role_id_permission"
        ),
    )
    op.create_index("ix_role_permissions_tenant_id", "role_permissions", ["tenant_id"])
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"])

    op.create_table(
        "user_roles",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", UUID, sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_id_role_id"),
    )
    op.create_index("ix_user_roles_tenant_id", "user_roles", ["tenant_id"])
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"])
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("family_id", UUID, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("revoked_reason", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_refresh_tokens_tenant_id", "refresh_tokens", ["tenant_id"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column(
            "actor_user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("actor_email", sa.String(320), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column(
            "changes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamps(),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_tenant_created", "audit_logs", ["tenant_id", "created_at"])
    op.create_index(
        "ix_audit_logs_tenant_resource",
        "audit_logs",
        ["tenant_id", "resource_type", "resource_id"],
    )

    op.create_table(
        "customers",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("notes", sa.String(2000), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "email", name="uq_customers_tenant_id_email"),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])
    op.create_index("ix_customers_tenant_name", "customers", ["tenant_id", "name"])

    op.create_table(
        "orders",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column(
            "customer_id",
            UUID,
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("total_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("placed_on", sa.Date(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "reference", name="uq_orders_tenant_id_reference"),
        sa.CheckConstraint("total_cents >= 0", name="ck_orders_total_non_negative"),
    )
    op.create_index("ix_orders_tenant_id", "orders", ["tenant_id"])
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_index("ix_orders_tenant_status", "orders", ["tenant_id", "status"])

    op.create_table(
        "order_items",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column(
            "order_id", UUID, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.CheckConstraint(
            "unit_price_cents >= 0", name="ck_order_items_unit_price_non_negative"
        ),
    )
    op.create_index("ix_order_items_tenant_id", "order_items", ["tenant_id"])
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def _create_app_role() -> None:
    """Provision the login role the application connects as.

    Idempotent, because a database may outlive the migration that first ran.
    The role is created without SUPERUSER, without BYPASSRLS and without
    CREATEDB: everything it may do, it may do because of an explicit GRANT
    below.
    """
    role = settings.app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Refusing to use {role!r} as a role name.")
    password = settings.app_db_password.replace("'", "''")

    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} LOGIN PASSWORD '{password}'
                        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                ELSE
                    ALTER ROLE {role} LOGIN PASSWORD '{password}'
                        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                END IF;
            END
            $$;
            """  # noqa: S608 - role name is validated above, password is escaped
        )
    )
    op.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
            f"TO {role}"
        )
    )
    # Future tables created by later migrations are covered without anybody
    # having to remember a GRANT.
    op.execute(
        sa.text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
        )
    )


def _enable_rls() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
            LANGUAGE sql STABLE AS $$
                SELECT nullif(current_setting('app.current_tenant', true), '')::uuid
            $$;
            """
        )
    )

    # The tenants table is scoped by its own primary key rather than by a
    # tenant_id column: a session may read the one row that defines it.
    _policy("tenants", "id = current_tenant_id()")

    for table in TENANT_SCOPED_TABLES:
        _policy(table, "tenant_id = current_tenant_id()")


def _policy(table: str, predicate: str) -> None:
    """Attach one policy to one table.

    ``USING`` filters what is readable and updatable; ``WITH CHECK`` filters
    what may be written. Both are needed: without ``WITH CHECK`` a caller could
    insert a row stamped with somebody else tenant id, and without ``USING``
    it could read one. With ``app.current_tenant`` unset both sides evaluate to
    NULL, which is not true, so an unbound session sees and writes nothing.
    """
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING ({predicate})
                WITH CHECK ({predicate})
            """
        )
    )


def upgrade() -> None:
    _create_tables()
    _create_app_role()
    _enable_rls()


def downgrade() -> None:
    for table in (*reversed(TENANT_SCOPED_TABLES), "tenants"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS current_tenant_id()"))
    for table in (
        "order_items",
        "orders",
        "customers",
        "audit_logs",
        "refresh_tokens",
        "user_roles",
        "role_permissions",
        "roles",
        "users",
        "tenants",
    ):
        op.drop_table(table)
