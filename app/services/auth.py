"""Authentication: login, refresh-token rotation, logout, password change.

No HTTP here. The service takes a slug, an email and a password and returns a
token pair; the router does nothing but hand it the request body. That is what
makes the interesting cases -- refresh-token reuse, a deactivated user, a
password change invalidating live sessions -- testable without a client.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import set_tenant_context
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.logging import get_logger, log_event
from app.core.principal import Principal
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from app.models.token import RefreshToken
from app.models.user import User
from app.repositories.token import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, PasswordChange, TokenPair
from app.services.audit import AuditService

logger = get_logger(__name__)
settings = get_settings()

# One sentence covers "no such tenant", "no such user", "wrong password" and
# "deactivated". Anything more specific is an account-existence oracle.
INVALID_CREDENTIALS = "Email or password is incorrect."


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)
        self.audit = AuditService(session)

    async def login(
        self,
        payload: LoginRequest,
        *,
        tenant_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
    ) -> TokenPair:
        """The tenant has already been resolved from the slug; from here on the
        session is pinned to it and cannot see another tenant users."""
        await set_tenant_context(self.session, tenant_id)

        user = await self.users.get_by_email(payload.email)
        if user is None:
            # Spend roughly the time a real verification costs, so a missing
            # account is not distinguishable by response latency.
            verify_password(payload.password, _DUMMY_HASH)
            log_event(logger, "auth.login_failed", reason="unknown_user")
            raise AuthenticationError(INVALID_CREDENTIALS)

        if not verify_password(payload.password, user.password_hash):
            log_event(logger, "auth.login_failed", reason="bad_password", user_id=str(user.id))
            raise AuthenticationError(INVALID_CREDENTIALS)

        if not user.is_active:
            log_event(logger, "auth.login_failed", reason="inactive", user_id=str(user.id))
            raise AuthenticationError(INVALID_CREDENTIALS)

        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(payload.password)

        pair = await self._issue_pair(user, ip_address=ip_address, user_agent=user_agent)
        await self.audit.record(
            tenant_id=tenant_id,
            action="auth.login",
            resource_type="user",
            resource_id=str(user.id),
            actor_email=user.email,
            ip_address=ip_address,
            request_id=request_id,
        )
        log_event(logger, "auth.login", user_id=str(user.id))
        return pair

    async def refresh(
        self,
        raw_token: str,
        *,
        tenant_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TokenPair:
        await set_tenant_context(self.session, tenant_id)
        stored = await self.tokens.get_by_hash(hash_refresh_token(raw_token))

        if stored is None:
            raise AuthenticationError("Refresh token is invalid.")

        if stored.revoked_at is not None:
            # Reuse detection. A consumed token presented again means a copy
            # escaped, so the entire family dies -- including the session
            # currently holding the live successor. The legitimate user is
            # logged out and notices, instead of silently sharing a session.
            revoked = await self.tokens.revoke_family(stored.family_id, "reuse_detected")
            log_event(
                logger,
                "auth.refresh_reuse_detected",
                user_id=str(stored.user_id),
                revoked=revoked,
            )
            await self.audit.record(
                tenant_id=tenant_id,
                action="auth.refresh_reuse_detected",
                resource_type="user",
                resource_id=str(stored.user_id),
                changes={"revoked_tokens": revoked},
                ip_address=ip_address,
            )
            # Committed before raising: the request session rolls back on an
            # exception, and a revocation that rolls back is no revocation.
            await self.session.commit()
            raise AuthenticationError("Refresh token has already been used.")

        if stored.expires_at <= datetime.now(UTC):
            raise AuthenticationError("Refresh token has expired.")

        user = await self.users.get_with_roles(stored.user_id)
        if user is None or not user.is_active:
            await self.tokens.revoke_family(stored.family_id, "user_inactive")
            raise AuthenticationError("Refresh token is invalid.")

        stored.revoked_at = datetime.now(UTC)
        stored.revoked_reason = "rotated"
        return await self._issue_pair(
            user,
            family_id=stored.family_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def logout(self, raw_token: str, *, tenant_id: uuid.UUID) -> None:
        await set_tenant_context(self.session, tenant_id)
        stored = await self.tokens.get_by_hash(hash_refresh_token(raw_token))
        if stored is not None and stored.revoked_at is None:
            await self.tokens.revoke_family(stored.family_id, "logout")

    async def logout_everywhere(self, principal: Principal) -> int:
        """Revoke every refresh token and every live access token.

        The second half is the token_version bump: without it an access token
        issued a minute ago would keep working for the rest of its lifetime
        after the user asked to be signed out everywhere.
        """
        user = await self.users.get_with_roles(principal.user_id)
        if user is None:
            raise AuthenticationError("Session is no longer valid.")
        user.token_version += 1
        revoked = await self.tokens.revoke_all_for_user(user.id, "logout_all")
        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="auth.logout_all",
            resource_type="user",
            resource_id=str(user.id),
            actor=principal,
            changes={"revoked_tokens": revoked},
        )
        return revoked

    async def change_password(self, principal: Principal, payload: PasswordChange) -> None:
        user = await self.users.get_with_roles(principal.user_id)
        if user is None:
            raise AuthenticationError("Session is no longer valid.")
        if not verify_password(payload.current_password, user.password_hash):
            raise PermissionDeniedError("Current password is incorrect.")

        user.password_hash = hash_password(payload.new_password)
        user.token_version += 1
        revoked = await self.tokens.revoke_all_for_user(user.id, "password_changed")
        await self.audit.record(
            tenant_id=principal.tenant_id,
            action="auth.password_changed",
            resource_type="user",
            resource_id=str(user.id),
            actor=principal,
            changes={"revoked_tokens": revoked},
        )
        log_event(logger, "auth.password_changed", user_id=str(user.id))

    async def _issue_pair(
        self,
        user: User,
        *,
        family_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TokenPair:
        access_token, ttl = create_access_token(
            user_id=user.id, tenant_id=user.tenant_id, token_version=user.token_version
        )
        raw_refresh = generate_refresh_token()
        self.session.add(
            RefreshToken(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=hash_refresh_token(raw_refresh),
                family_id=family_id or uuid.uuid4(),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=settings.refresh_token_ttl_seconds),
                user_agent=(user_agent or "")[:255] or None,
                ip_address=(ip_address or "")[:45] or None,
            )
        )
        await self.session.flush()
        return TokenPair(access_token=access_token, refresh_token=raw_refresh, expires_in=ttl)


# A syntactically valid Argon2 hash of an unguessable value, used only to burn
# the CPU time a real verification would for an account that does not exist.
_DUMMY_HASH = hash_password(uuid.uuid4().hex)
