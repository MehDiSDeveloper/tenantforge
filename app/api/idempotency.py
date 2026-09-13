"""The HTTP side of idempotency keys: the header in, the replayed response out.

The rules live in ``app/services/idempotency.py``. A create endpoint opts in
with three lines::

    if (replayed := await idempotency.replay(principal, payload)) is not None:
        return replayed
    ...                                        # do the work, set headers
    await idempotency.record(result, response, status.HTTP_201_CREATED)

Without the header both calls are no-ops, so existing clients are unaffected.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.core.principal import Principal
from app.services.idempotency import IdempotencyService, StoredResponse

REPLAYED_HEADER = "Idempotent-Replayed"

#: Response headers worth replaying. Everything else is per-delivery.
_STORED_HEADERS = ("ETag",)

IDEMPOTENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {"description": "A request with this Idempotency-Key is still in flight."},
    422: {"description": "The Idempotency-Key was already used for a different request."},
}


class Idempotency:
    def __init__(self, service: IdempotencyService, key: str | None, scope: str) -> None:
        self.service = service
        self.key = key
        self.scope = scope
        self._claim: UUID | None = None

    async def replay(self, principal: Principal, payload: BaseModel) -> Response | None:
        """The stored response for a retry, or ``None`` if this request should run."""
        if self.key is None:
            return None
        outcome = await self.service.claim(principal, self.key, self.scope, payload)
        if isinstance(outcome, StoredResponse):
            return JSONResponse(
                outcome.body,
                status_code=outcome.status_code,
                headers={**outcome.headers, REPLAYED_HEADER: "true"},
            )
        self._claim = outcome
        return None

    async def record(self, body: BaseModel, response: Response, status_code: int) -> None:
        if self._claim is None:
            return
        headers = {
            name: response.headers[name] for name in _STORED_HEADERS if name in response.headers
        }
        await self.service.record(
            self._claim, StoredResponse(status_code, body.model_dump(mode="json"), headers)
        )


async def get_idempotency(
    request: Request,
    session: SessionDep,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="A unique value per logical operation, such as a UUID. Retrying "
            "with the same key returns the original response instead of repeating the "
            "write. Keys expire after 24 hours.",
        ),
    ] = None,
) -> Idempotency:
    return Idempotency(
        IdempotencyService(session), idempotency_key, f"{request.method} {request.url.path}"
    )


IdempotencyDep = Annotated[Idempotency, Depends(get_idempotency)]
