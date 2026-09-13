"""Idempotency keys: a retried create is answered, never repeated.

A client whose ``POST /orders`` times out cannot know whether the order was
placed. Without a key it chooses between a possible duplicate and a possible
loss. With ``Idempotency-Key`` it simply retries: the first request to commit
stores its response, and every retry with the same key gets that response back
instead of running again.

The claim is a row in ``idempotency_keys`` inserted in the *same transaction*
as the write it guards. That buys two properties without a lock manager:

* **Atomicity.** If the write rolls back, so does the claim, and a retry runs
  for real. A key is only ever spent on a request that committed.
* **Serialised retries.** A retry that arrives while the original is still in
  flight inserts into the same unique index, and PostgreSQL makes it wait for
  the original's transaction. If that commits, the retry conflicts and replays
  the stored response; if it rolls back, the retry takes the claim over.

A key is bound to the endpoint and payload it was first used with. Reusing it
for a different request is a client bug, and is refused rather than answered
with a response describing some other request.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, IdempotencyKeyReusedError, ValidationError
from app.core.principal import Principal
from app.repositories.idempotency import IdempotencyKeyRepository

#: Visible ASCII. Room for a UUID, a ULID, or whatever the client already has.
_KEY_PATTERN = re.compile(r"[\x21-\x7e]{1,255}")


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: Any
    headers: dict[str, str]


def fingerprint(scope: str, payload: BaseModel) -> str:
    """What a key is bound to: the endpoint and the *validated* payload.

    Hashing the parsed model rather than the raw bytes means a retry that
    re-serialises the same JSON with other whitespace or key order is still
    recognised as the same request.
    """
    canonical = json.dumps(
        {"scope": scope, "payload": payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyService:
    def __init__(self, session: AsyncSession, *, ttl_seconds: int | None = None) -> None:
        self.keys = IdempotencyKeyRepository(session)
        if ttl_seconds is None:
            ttl_seconds = get_settings().idempotency_key_ttl_seconds
        self.ttl = timedelta(seconds=ttl_seconds)

    async def claim(
        self, principal: Principal, key: str, scope: str, payload: BaseModel
    ) -> UUID | StoredResponse:
        """Claim ``key`` for this request, or return the response it already earned."""
        if not _KEY_PATTERN.fullmatch(key):
            raise ValidationError("Idempotency-Key must be 1-255 visible ASCII characters.")
        request_hash = fingerprint(scope, payload)

        # An expired key is forgotten, not refused: the client may reuse it.
        await self.keys.purge_expired(
            principal.user_id, key, older_than=datetime.now(UTC) - self.ttl
        )
        claimed = await self.keys.claim(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            key=key,
            scope=scope,
            request_hash=request_hash,
        )
        if claimed is not None:
            return claimed

        existing = await self.keys.find(principal.user_id, key)
        if existing is not None and existing.request_hash != request_hash:
            raise IdempotencyKeyReusedError(
                "This Idempotency-Key was already used for a different request.",
                extra={"scope": existing.scope},
            )
        if existing is None or existing.response_status is None:
            # Not reachable while the claim and the write share a transaction;
            # kept so a change that splits them fails loudly instead of
            # replaying an empty answer.
            raise ConflictError(
                "A request with this Idempotency-Key is still being processed. Retry shortly."
            )
        return StoredResponse(
            status_code=existing.response_status,
            body=existing.response_body,
            headers=dict(existing.response_headers or {}),
        )

    async def record(self, claim_id: UUID, response: StoredResponse) -> None:
        await self.keys.store_response(
            claim_id, response.status_code, response.body, response.headers
        )
