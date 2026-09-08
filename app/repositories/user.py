from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from app.core.pagination import PageParams
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    def _base(self) -> Select[tuple[User]]:
        return select(User).options(selectinload(User.roles).selectinload(Role.permissions))

    async def get_with_roles(self, user_id: UUID) -> User | None:
        return await self.session.scalar(self._base().where(User.id == user_id))

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(self._base().where(User.email == email.lower()))

    async def search(self, params: PageParams, query: str | None = None) -> tuple[list[User], int]:
        stmt = self._base()
        if query:
            pattern = f"%{query.lower()}%"
            stmt = stmt.where(
                func.lower(User.email).like(pattern) | func.lower(User.full_name).like(pattern)
            )
        total = await self.count(stmt)
        stmt = stmt.order_by(User.created_at.desc(), User.id.desc())
        return await self.paginate(stmt, params), total

    async def assign_roles(self, user: User, roles: list[Role]) -> None:
        """Replace the user role set.

        A delete-then-insert rather than a diff: the set is tiny, and the
        obvious version has no ordering bug to find later.
        """
        existing = await self.session.scalars(select(UserRole).where(UserRole.user_id == user.id))
        for row in existing:
            await self.session.delete(row)
        await self.session.flush()
        for role in roles:
            self.session.add(
                UserRole(tenant_id=user.tenant_id, user_id=user.id, role_id=role.id)
            )
        await self.session.flush()
        await self.session.refresh(user, attribute_names=["roles"])
