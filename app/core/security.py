"""Password hashing and JWT minting.

Argon2id via ``argon2-cffi`` directly -- no passlib shim -- and PyJWT for the
access token. Refresh tokens are *not* JWTs: they are opaque random strings
whose SHA-256 digest is stored, so a database leak yields no usable credential
and revocation is a row update rather than a denylist of signatures.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import AuthenticationError

_settings = get_settings()

# OWASP "second recommended" Argon2id profile: 19 MiB, t=2, p=1.
_hasher: Final = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

REFRESH_TOKEN_BYTES: Final = 32
ACCESS_TOKEN_TYPE: Final = "access"  # noqa: S105 - a claim value, not a secret


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """Keyed digest, so the stored value is useless without ``SECRET_KEY``."""
    return hmac.new(_settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: UUID
    tenant_id: UUID
    token_version: int
    expires_at: datetime


def create_access_token(*, user_id: UUID, tenant_id: UUID, token_version: int) -> tuple[str, int]:
    """Return ``(jwt, ttl_seconds)``.

    ``tv`` (token version) is what makes a short-lived stateless token
    revocable: bumping ``users.token_version`` invalidates every access token
    already issued to that user, with no denylist to keep.
    """
    now = datetime.now(UTC)
    ttl = _settings.access_token_ttl_seconds
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "tv": token_version,
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, _settings.secret_key, algorithm=_settings.jwt_algorithm)
    return token, ttl


def decode_access_token(token: str) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            _settings.secret_key,
            algorithms=[_settings.jwt_algorithm],
            options={"require": ["exp", "sub", "tid", "tv", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Access token is invalid.") from exc

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise AuthenticationError("Access token is invalid.")

    try:
        return AccessTokenClaims(
            user_id=UUID(payload["sub"]),
            tenant_id=UUID(payload["tid"]),
            token_version=int(payload["tv"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Access token is invalid.") from exc
