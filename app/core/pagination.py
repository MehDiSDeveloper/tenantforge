"""Offset pagination, one shape for every list endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


@dataclass(frozen=True, slots=True)
class PageParams:
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(description="Total rows visible to the caller's tenant.")
    limit: int
    offset: int

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> Page[T]:
        return cls(items=items, total=total, limit=params.limit, offset=params.offset)
