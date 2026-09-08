from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update

from app.models.token import RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )

    async def revoke_family(self, family_id: UUID, reason: str) -> int:
        """Revoke every live token in a family. Returns the number affected."""
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def revoke_all_for_user(self, user_id: UUID, reason: str) -> int:
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
        )
        await self.session.flush()
        return int(result.rowcount or 0)
