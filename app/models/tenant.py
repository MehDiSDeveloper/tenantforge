from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A customer organisation. Its ``id`` is the isolation key of the system."""

    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    users: Mapped[list[User]] = relationship(back_populates="tenant", lazy="raise")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tenant {self.slug}>"
