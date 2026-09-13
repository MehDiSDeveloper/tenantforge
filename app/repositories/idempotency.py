from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models.idempotency import IdempotencyKey
from app.repositories.base import BaseRepository


class IdempotencyKeyRepository(BaseRepository[IdempotencyKey]):
    model = IdempotencyKey

    async def purge_expired(self, user_id: UUID, key: str, *, older_than: datetime) -> None:
        await self.session.execute(
            delete(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.key == key,
                IdempotencyKey.created_at < older_than,
            )
        )

    async def claim(
        self, *, tenant_id: UUID, user_id: UUID, key: str, scope: str, request_hash: str
    ) -> UUID | None:
        """``INSERT ... ON CONFLICT DO NOTHING``; the new row's id, or ``None``.

        If another transaction holds an uncommitted claim on the same key,
        PostgreSQL blocks this statement until that transaction finishes. That
        wait is what serialises concurrent retries.
        """
        stmt = (
            insert(IdempotencyKey)
            .values(
                tenant_id=tenant_id,
                user_id=user_id,
                key=key,
                scope=scope,
                request_hash=request_hash,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "user_id", "key"])
            .returning(IdempotencyKey.id)
        )
        claimed: UUID | None = await self.session.scalar(stmt)
        return claimed

    async def find(self, user_id: UUID, key: str) -> IdempotencyKey | None:
        found: IdempotencyKey | None = await self.session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id, IdempotencyKey.key == key
            )
        )
        return found

    async def store_response(
        self, claim_id: UUID, status_code: int, body: Any, headers: dict[str, str]
    ) -> None:
        await self.session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == claim_id)
            .values(response_status=status_code, response_body=body, response_headers=headers)
        )
