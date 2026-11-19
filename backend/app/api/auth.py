import logging
import re
import secrets
import unicodedata
from typing import Annotated

import redis.asyncio as _aioredis
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_write_db
from app.models.enums import UserRole
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
)
from app.schemas.user import UserCreate, UserInvite, UserResponse, UserUpdate
from app.services import email_verification_service
from app.services.audit_service import log_audit_event
from app.utils.auth import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_user_from_refresh_token,
    hash_password,
    require_role,
    revoke_token,
    verify_password,
)
from app.utils.client_ip import get_request_client_ip
from app.utils.rate_limit import enforce_fixed_window_limit
from app.utils.tenant_context import get_current_tenant_id, tenant_filter

logger = logging.getLogger(__name__)

_pw_reset_redis: _aioredis.Redis | None = None


async def _get_pw_reset_redis() -> _aioredis.Redis:
    global _pw_reset_redis
    if _pw_reset_redis is None:
        _pw_reset_redis = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _pw_reset_redis


_PW_RESET_TTL_SECONDS = 1800   # 30 minutes
_INVITE_TTL_SECONDS    = 259200  # 72 hours

router = APIRouter()
bearer = HTTPBearer()

_SPECIAL_CHARS = frozenset(r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~""")


def _validate_password_strength(password: str) -> None:
    """Raise HTTP 400 if the password does not meet complexity requirements."""
    errors: list[str] = []
    if len(password) < 8:
        errors.append("at least 8 characters")
    if not any(c.isupper() for c in password):
        errors.append("at least one uppercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("at least one digit")
    if not any(c in _SPECIAL_CHARS for c in password):
        errors.append("at least one special character")
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must contain: {', '.join(errors)}",
        )


def _slugify_company_name(company_name: str) -> str:
    normalized = unicodedata.normalize("NFKD", company_name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "tenant"


async def _generate_unique_tenant_slug(db: AsyncSession, company_name: str) -> str:
    base_slug = _slugify_company_name(company_name)
    slug = base_slug
    suffix = 2

    while True:
        result = await db.execute(select(Tenant.id).where(Tenant.slug == slug))
        if result.scalar_one_or_none() is None:
            return slug
        slug = f"{base_slug}-{suffix}"
        suffix += 1


async def _get_tenant_by_slug(db: AsyncSession, tenant_slug: str) -> Tenant:
    normalized_slug = _slugify_company_name(tenant_slug)
    result = await db.execute(
        select(Tenant).where(Tenant.slug == normalized_slug).execution_options(skip_tenant_scope=True)
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown workspace",
        )
    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace is disabled",
        )
    return tenant


async def _resolve_login_user(
    db: AsyncSession,
    email: str,
    password: str,
    tenant_slug: str | None,
) -> User | None:
    query = select(User).where(User.email == email).execution_options(skip_tenant_scope=True)
    if tenant_slug:
        tenant = await _get_tenant_by_slug(db, tenant_slug)
        query = query.where(User.tenant_id == tenant.id)

    result = await db.execute(query)
    candidates = list(result.scalars().all())
    matches = [user for user in candidates if verify_password(password, user.hashed_password)]
    if not matches:
        return None
    if len(matches) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multiple accounts match this email. Include tenant_slug to select a workspace.",
        )
    return matches[0]


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    data: SignupRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Create a tenant and owner account, then return an authenticated token pair."""
    client_ip = get_request_client_ip(request, default="unknown")
    await enforce_fixed_window_limit(
        key=f"rl:auth:signup:{client_ip}",
        limit=settings.AUTH_SIGNUP_RATE_LIMIT_PER_MIN,
        window_seconds=60,
    )
    _validate_password_strength(data.password)

    tenant = Tenant(
        name=data.company_name.strip(),
        slug=await _generate_unique_tenant_slug(db, data.company_name),
        is_active=True,
    )
    db.add(tenant)
    await db.flush()

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        role=UserRole.OWNER,
        tenant_id=tenant.id,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # Send verification email (non-blocking; errors are logged, not raised)
    try:
        await email_verification_service.send_verification_email(db, user)
    except Exception:
        logger.exception("Failed to send verification email to %s", user.email)

    await log_audit_event(
        db,
        action="auth.signup",
        actor_user_id=user.id,
        tenant_id=tenant.id,
        resource_type="tenant",
        resource_id=str(tenant.id),
        details={
            "company_name": tenant.name,
            "tenant_slug": tenant.slug,
            "full_name": data.full_name,
        },
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role.value, str(user.tenant_id)),
        refresh_token=create_refresh_token(str(user.id), user.role.value, str(user.tenant_id)),
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    data: UserCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
):
    """Create a new user (admin only)."""
    _validate_password_strength(data.password)

    # Check uniqueness within the admin's tenant.
    result = await db.execute(
        select(User).where(
            User.email == data.email,
            tenant_filter(User, _admin),
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        role=data.role,
        tenant_id=get_current_tenant_id(_admin),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    await log_audit_event(
        db,
        action="auth.register",
        actor_user_id=_admin.id,
        tenant_id=get_current_tenant_id(_admin),
        resource_type="user",
        resource_id=str(user.id),
    )
    return user


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """List all users (admin only)."""
    count_result = await db.execute(
        select(sa_func.count()).select_from(User).where(tenant_filter(User, current_user))
    )
    total = int(count_result.scalar_one())
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    result = await db.execute(
        select(User)
        .where(tenant_filter(User, current_user))
        .order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    data: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
):
    """Update a user's role or active status (admin only, same tenant)."""
    import uuid as _uuid
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid user_id")

    result = await db.execute(
        select(User).where(
            User.id == uid,
            tenant_filter(User, admin),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Prevent admins from deactivating themselves.
    if data.is_active is False and user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active

    await db.flush()
    await db.refresh(user)
    await log_audit_event(
        db,
        action="auth.update_user",
        actor_user_id=admin.id,
        tenant_id=get_current_tenant_id(admin),
        resource_type="user",
        resource_id=str(user.id),
    )
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Authenticate and return JWT tokens."""
    client_ip = get_request_client_ip(request, default="unknown")
    rate_limit_slug = data.tenant_slug.strip().lower() if data.tenant_slug else "any"
    await enforce_fixed_window_limit(
        key=f"rl:auth:login:{client_ip}:{data.email}:{rate_limit_slug}",
        limit=settings.AUTH_RATE_LIMIT_PER_MIN,
        window_seconds=60,
    )

    user = await _resolve_login_user(db, data.email, data.password, data.tenant_slug)
    if not user:
        await log_audit_event(
            db,
            action="auth.login_failed",
            tenant_id=None,
            resource_type="user",
            resource_id=data.email,
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    # Enforce email verification if configured (grace period applies to new accounts)
    email_verification_service.enforce_email_verified(user)

    await log_audit_event(
        db,
        action="auth.login",
        actor_user_id=user.id,
        tenant_id=user.tenant_id,
        resource_type="user",
        resource_id=str(user.id),
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role.value, str(user.tenant_id)),
        refresh_token=create_refresh_token(str(user.id), user.role.value, str(user.tenant_id)),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    data: RefreshRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Rotate refresh token and issue a fresh token pair."""
    client_ip = get_request_client_ip(request, default="unknown")
    await enforce_fixed_window_limit(
        key=f"rl:auth:refresh:{client_ip}",
        limit=settings.AUTH_RATE_LIMIT_PER_MIN,
        window_seconds=60,
    )
    user = await get_user_from_refresh_token(data.refresh_token, db)
    await revoke_token(data.refresh_token)
    await log_audit_event(
        db,
        action="auth.refresh",
        actor_user_id=user.id,
        tenant_id=user.tenant_id,
        resource_type="user",
        resource_id=str(user.id),
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role.value, str(user.tenant_id)),
        refresh_token=create_refresh_token(str(user.id), user.role.value, str(user.tenant_id)),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: Annotated[User, Depends(get_current_user)]):
    """Get current authenticated user."""
    return current_user


@router.post("/invite", status_code=status.HTTP_202_ACCEPTED)
async def invite_user(
    data: UserInvite,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
):
    """Invite a new user by email. Creates account in pending state and sends an email with an activation link."""
    import json as _json  # noqa: PLC0415

    email_normalized = data.email.strip().lower()

    existing = await db.execute(
        select(User).where(
            User.email == email_normalized,
            tenant_filter(User, admin),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered in this workspace")

    placeholder_pw = secrets.token_hex(32)
    user = User(
        email=email_normalized,
        hashed_password=hash_password(placeholder_pw),
        role=data.role,
        is_active=False,
        tenant_id=get_current_tenant_id(admin),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    token = secrets.token_urlsafe(32)
    r = await _get_pw_reset_redis()
    payload = _json.dumps({"user_id": str(user.id), "tenant_id": str(user.tenant_id)})
    await r.set(f"invite:{token}", payload, ex=_INVITE_TTL_SECONDS)

    from app.services.notification_service import _send_email  # noqa: PLC0415
    invite_url = (
        f"{settings.FRONTEND_URL}/accept-invite?token={token}"
        if getattr(settings, "FRONTEND_URL", None)
        else f"(token: {token})"
    )
    await _send_email(
        recipient=user.email,
        subject="You've been invited to join the workspace",
        body=(
            f"Hi,\n\nYou've been invited to join the workspace. "
            f"Click the link below to set your password and activate your account (valid for 72 hours):\n\n"
            f"{invite_url}\n\n"
            "If you did not expect this invitation, you can safely ignore this email."
        ),
    )

    await log_audit_event(
        db,
        action="auth.invite_user",
        actor_user_id=admin.id,
        tenant_id=get_current_tenant_id(admin),
        resource_type="user",
        resource_id=str(user.id),
        details={"email": user.email, "role": data.role.value},
        ip_address=get_request_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "invited"}


@router.post("/accept-invite", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def accept_invite(
    token: Annotated[str, Body(embed=True)],
    new_password: Annotated[str, Body(embed=True)],
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Accept an invitation: set password and activate the account. Returns token pair so the user is logged in immediately."""
    import json as _json  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    r = await _get_pw_reset_redis()
    raw = await r.get(f"invite:{token}")
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired invitation link")

    try:
        invite_data = _json.loads(raw)
        user_id = _uuid.UUID(invite_data["user_id"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired invitation link")

    user = await db.get(User, user_id)
    if not user or user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired invitation link")

    _validate_password_strength(new_password)

    user.hashed_password = hash_password(new_password)
    user.is_active = True
    await r.delete(f"invite:{token}")
    await db.flush()
    await db.refresh(user)

    await log_audit_event(
        db,
        action="auth.accept_invite",
        actor_user_id=user.id,
        tenant_id=user.tenant_id,
        resource_type="user",
        resource_id=str(user.id),
    )
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role.value, str(user.tenant_id)),
        refresh_token=create_refresh_token(str(user.id), user.role.value, str(user.tenant_id)),
    )


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    email: Annotated[str, Body(embed=True)],
    request: Request,
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Request a password-reset link. Always responds 202 to avoid email enumeration."""
    client_ip = get_request_client_ip(request, default="unknown")
    await enforce_fixed_window_limit(
        key=f"rl:auth:forgot:{client_ip}",
        limit=5,
        window_seconds=60,
    )

    result = await db.execute(select(User).where(User.email == email.strip().lower()))
    user = result.scalar_one_or_none()

    if user and user.is_active:
        token = secrets.token_urlsafe(32)
        r = await _get_pw_reset_redis()
        await r.set(f"pw_reset:{token}", str(user.id), ex=_PW_RESET_TTL_SECONDS)

        from app.services.notification_service import _send_email  # noqa: PLC0415
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}" if getattr(settings, "FRONTEND_URL", None) else f"(token: {token})"
        await _send_email(
            recipient=user.email,
            subject="Password reset request",
            body=(
                f"Hi,\n\nClick the link below to reset your password (valid for 30 minutes):\n\n{reset_url}\n\n"
                "If you did not request this, you can safely ignore this email."
            ),
        )

    return {"status": "accepted"}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    token: Annotated[str, Body(embed=True)],
    new_password: Annotated[str, Body(embed=True)],
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Consume a password-reset token and set a new password."""
    _validate_password_strength(new_password)

    r = await _get_pw_reset_redis()
    user_id_str = await r.get(f"pw_reset:{token}")
    if not user_id_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset token")

    import uuid as _uuid  # noqa: PLC0415
    user = await db.get(User, _uuid.UUID(user_id_str))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset token")

    user.hashed_password = hash_password(new_password)
    await r.delete(f"pw_reset:{token}")
    await db.flush()
    await log_audit_event(
        db,
        action="auth.password_reset",
        actor_user_id=user.id,
        tenant_id=user.tenant_id,
        resource_type="user",
        resource_id=str(user.id),
    )
    return {"status": "ok"}


@router.post("/logout")
async def logout(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
    payload: Annotated[LogoutRequest | None, Body()] = None,
):
    """Revoke current access token and optional refresh token."""
    await revoke_token(credentials.credentials)
    if payload and payload.refresh_token:
        await revoke_token(payload.refresh_token)
    await log_audit_event(
        db,
        action="auth.logout",
        actor_user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        resource_type="user",
        resource_id=str(current_user.id),
        ip_address=get_request_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "ok"}


@router.get("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    token: Annotated[str, Query(description="Raw email verification token from the link")],
    db: Annotated[AsyncSession, Depends(get_write_db)],
):
    """Verify a user's email address using the token sent on signup."""
    user = await email_verification_service.verify_email_token(db, token)
    await log_audit_event(
        db,
        action="auth.email_verified",
        actor_user_id=user.id,
        tenant_id=user.tenant_id,
        resource_type="user",
        resource_id=str(user.id),
    )
    return {"status": "ok", "email": user.email}


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Re-send the email verification link to the authenticated user."""
    await email_verification_service.resend_verification_email(db, current_user)
    return {"status": "ok"}
