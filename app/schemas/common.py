from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Read models. ``from_attributes`` is the only reason they differ from
    write models, and keeping the two apart is what stops a column like
    ``password_hash`` from ever reaching a response by accident."""

    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str


class ErrorResponse(BaseModel):
    code: str
    detail: str
    request_id: str | None = None
