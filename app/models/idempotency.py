from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IdempotencyKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A client-chosen key, the request it was spent on, and the answer given.

    The row is inserted in the same transaction as the write it guards, so a
    key exists only for requests that committed; see
    ``app/services/idempotency.py``. Keys are scoped to one user in one tenant,
    and the table is under the same RLS policy as everything else, so a key can
    never replay another workspace's response.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "key", name="uq_idempotency_keys_tenant_id_user_id_key"
        ),
        # For a retention job; lookups go through the unique index.
        Index("ix_idempotency_keys_created_at", "created_at"),
    )
    __tenant_scoped__ = True

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    # "POST /api/v1/orders" -- kept for whoever is reading the table at 3am.
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    # SHA-256 of the scope and the validated payload.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    response_status: Mapped[int | None] = mapped_column(Integer, default=None)
    response_body: Mapped[Any] = mapped_column(JSONB, nullable=True, default=None)
    response_headers: Mapped[dict[str, str] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
