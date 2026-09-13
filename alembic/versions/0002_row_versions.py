"""Row versions for optimistic concurrency on customers and orders.

A constant ``server_default`` backfills every existing row as version 1 without
a table rewrite (PostgreSQL 11+ stores it in the catalogue), so this is safe to
run against a populated database. Column-level access is inherited from the
table grants made in 0001, and the RLS policies are untouched.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERSIONED_TABLES = ("customers", "orders")


def upgrade() -> None:
    for table in VERSIONED_TABLES:
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    for table in VERSIONED_TABLES:
        op.drop_column(table, "version")
