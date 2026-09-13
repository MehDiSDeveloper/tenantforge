"""Optimistic concurrency: row versions, ETags and ``If-Match``.

Two admins open the same customer, both edit, both save. Without a version the
second save silently erases the first -- nothing errors, nobody notices, and
the audit trail dutifully records both. With one, the second save is refused
and its client is told to reload.

The check happens at two distances:

* **Inside a request.** Versioned tables map ``version`` as SQLAlchemy's
  ``version_id_col``, so every ``UPDATE``/``DELETE`` carries
  ``WHERE version = <what we loaded>`` and bumps it. A write that raced ours
  between our ``SELECT`` and our ``UPDATE`` matches zero rows, and
  :func:`stale_write_guard` turns that into a 409.
* **Across the gap between a client's read and its write**, which is where the
  real conflicts happen. Reads return the version as an ``ETag``; a write that
  sends it back in ``If-Match`` is refused with 412 if the row has moved on.

``If-Match`` is optional, so existing clients keep working unchanged -- they
simply keep last-write-wins until they opt in.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm.exc import StaleDataError

from app.core.errors import ConflictError, PreconditionFailedError

#: The versions a write will accept, or ``None`` for "no precondition".
ExpectedVersions = frozenset[int] | None


def etag(version: int) -> str:
    return f'"{version}"'


def parse_if_match(header: str | None) -> ExpectedVersions:
    """The versions an ``If-Match`` header accepts.

    ``None`` means no precondition: the header is absent, blank or ``*``. An
    empty set means a header *was* sent but names no version this API could
    have issued, and RFC 9110 says that must fail rather than be ignored -- a
    client that asked for a precondition gets one.

    Weak validators (``W/"3"``) are accepted as if strong. The tag names a row
    version, not a byte digest, so a proxy that weakened it in transit (nginx
    does when it compresses) has not changed what it identifies.
    """
    if header is None or not header.strip():
        return None
    versions: set[int] = set()
    for raw in header.split(","):
        tag = raw.strip()
        if tag == "*":
            return None
        tag = tag.removeprefix("W/")
        if len(tag) >= 2 and tag[0] == tag[-1] == '"':
            tag = tag[1:-1]
        if tag.isascii() and tag.isdigit():
            versions.add(int(tag))
    return frozenset(versions)


def check_version(current: int, expected: ExpectedVersions, resource: str) -> None:
    if expected is not None and current not in expected:
        raise PreconditionFailedError(
            f"This {resource} has changed since you read it. Reload and try again.",
            extra={"current_version": current},
        )


@contextmanager
def stale_write_guard(resource: str) -> Iterator[None]:
    """Wrap the flush of a versioned write."""
    try:
        yield
    except StaleDataError as exc:
        raise ConflictError(
            f"This {resource} was changed by another request at the same moment. "
            "Reload and try again."
        ) from exc
