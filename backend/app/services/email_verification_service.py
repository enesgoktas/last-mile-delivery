"""Email verification service (Phase 8.6).

Flow:
1. On signup, ``send_verification_email`` is called — it generates a token,
   persists it on the User row, and dispatches the verification email via
   the notification service.
2. The customer clicks the link in the email (frontend calls
   ``/api/v1/auth/verify-email?token=<raw_token>``).
3. ``verify_email_token`` looks up the user by the raw token, checks it
   hasn't expired, sets ``email_verified=True``, and clears the token.
4. Login: ``enforce_email_verified`` raises 403 if the user account has
   ``REQUIRE_EMAIL_VERIFICATION=True`` and the email isn't verified yet,
   with a configurable grace-period window so existing accounts aren't
   locked out on first deploy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.utils.compat import UTC

logger = logging.getLogger(__name__)

# How long a verification token stays valid (resend resets this clock)
_TOKEN_TTL_HOURS: int = 48
# How long after account creation the login grace period applies before
# enforcement kicks in. Protects existing users on first deploy.
_GRACE_PERIOD_HOURS: int = 72


async def send_verification_email(db: AsyncSession, user: User) -> None:
    """Generate a verification token and send the email.

    The actual send is delegated to the notification service.  If
    ``NOTIFICATION_DRY_RUN`` is True the link is just logged instead of
    sent — consistent with the rest of the notification plumbing.
    """
    raw_token = user.generate_verification_token()
    await db.flush()

    verify_url = (
        f"{settings.FRONTEND_URL}/verify-email?token={raw_token}"
    )

    if settings.NOTIFICATION_DRY_RUN:
        logger.info(
            "[DRY RUN] Email verification link for %s: %s",
            user.email,
            verify_url,
        )
        return

    # Delegate to the notification service to send the actual email.
    # Import here to avoid circular imports at module level.
    from app.services import notification_service  # noqa: PLC0415

    await notification_service.send_transactional_email(
        db=db,
        tenant_id=user.tenant_id,
        recipient_email=user.email,
        subject="Verify your email address",
        body_text=(
            f"Hi,\n\n"
            f"Please verify your email by clicking the link below:\n\n"
            f"{verify_url}\n\n"
            f"This link expires in {_TOKEN_TTL_HOURS} hours.\n\n"
            f"If you did not sign up, you can safely ignore this email."
        ),
        template_name="email_verification",
        template_vars={"verify_url": verify_url, "ttl_hours": _TOKEN_TTL_HOURS},
    )


async def verify_email_token(db: AsyncSession, token: str) -> User:
    """Find the user by raw token, validate TTL, mark verified, clear token.

    Raises HTTPException:
      400 — token missing / expired / invalid
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token is required",
        )

    result = await db.execute(
        select(User).where(User.email_verification_token == token)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or already-used verification token",
        )

    # Check TTL
    if user.email_verification_sent_at:
        expires_at = user.email_verification_sent_at + timedelta(hours=_TOKEN_TTL_HOURS)
        if datetime.now(UTC).replace(tzinfo=None) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification token has expired. Please request a new one.",
            )

    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_sent_at = None
    await db.flush()

    logger.info("Email verified for user %s (tenant %s)", user.email, user.tenant_id)
    return user


async def resend_verification_email(db: AsyncSession, user: User) -> None:
    """Regenerate a verification token and re-send the email."""
    if user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already verified",
        )
    await send_verification_email(db, user)


def enforce_email_verified(user: User) -> None:
    """Raise 403 if email verification is required and not yet completed.

    A grace period of ``_GRACE_PERIOD_HOURS`` hours after account creation
    allows pre-existing users to log in and complete verification without
    being hard-locked on first deploy.

    The guard is a no-op when ``REQUIRE_EMAIL_VERIFICATION=False`` (default),
    so it can be enabled per deployment without breaking existing users.
    """
    if not getattr(settings, "REQUIRE_EMAIL_VERIFICATION", False):
        return

    if user.email_verified:
        return

    # Apply grace period: let the user in for a while to complete verification
    grace_cutoff = user.created_at + timedelta(hours=_GRACE_PERIOD_HOURS)
    if datetime.now(UTC).replace(tzinfo=None) < grace_cutoff:
        logger.warning(
            "User %s has not verified email but is within grace period.", user.email
        )
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Email address not verified. "
            "Please check your inbox for the verification link or request a new one."
        ),
    )
