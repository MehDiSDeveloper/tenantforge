from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.common import ORMModel


class AuditLogRead(ORMModel):
    id: UUID
    tenant_id: UUID
    actor_user_id: UUID | None
    actor_email: str | None
    action: str
    resource_type: str
    resource_id: str | None
    ip_address: str | None
    request_id: str | None
    changes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
