import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]

from app.api import (
    admin,
    analytics,
    api_keys,
    audit,
    auth,
    branding,
    constraints,
    couriers,
    depot_edges,
    depots,
    dispatch,
    geocode,
    oidc,
    operations_settings,
    orders,
    packages,
    rbac,
    recurring_orders,
    routes,
    sla_alerts,
    superadmin,
    tracking,
    traffic_priors,
    vehicle_specs,
    vehicle_tags,
    webhooks,
)
from app.config import settings
from app.database import engine
from app.utils.query_profiler import (
    clear_request_query_counters,
    install_query_profiler,
)
from app.utils.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    bind_request_context,
    configure_request_id_logging,
    correlation_id_from_headers,
    normalize_request_id,
)
from app.utils.tenant_context import reset_current_tenant_scope, set_current_tenant_scope

configure_request_id_logging()
logger = logging.getLogger(__name__)

# Install query profiler if enabled (8.10)
if settings.ENABLE_QUERY_PROFILER:
    import app.utils.query_profiler as _qp  # noqa: PLC0415
    _qp.SLOW_QUERY_THRESHOLD_MS = settings.SLOW_QUERY_THRESHOLD_MS
    _qp.N1_THRESHOLD = settings.N1_DETECTION_THRESHOLD
    install_query_profiler(engine)
    logger.info(
        "Query profiler enabled (slow_query_threshold=%.0fms, n1_threshold=%d)",
        settings.SLOW_QUERY_THRESHOLD_MS,
        settings.N1_DETECTION_THRESHOLD,
    )

if sentry_sdk is not None and settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )
    logger.info("Sentry initialized (environment=%s)", settings.ENVIRONMENT)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await startup_checks()
    yield


app = FastAPI(
    title="Last-Mile Delivery System",
    description="Production-ready last-mile delivery management platform",
    version="1.0.0",
    lifespan=lifespan,
)

_DEV_LAN_CORS_REGEX = (
    r"^https?://"
    r"(?:(?:localhost|127\.0\.0\.1)|"
    r"(?:10\.\d+\.\d+\.\d+)|"
    r"(?:192\.168\.\d+\.\d+)|"
    r"(?:172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+))"
    r"(?::(?:5173|5174|5175|19006))$"
)


def resolve_cors_middleware_settings() -> tuple[list[str], str | None, bool, list[str], list[str]]:
    origins = settings.cors_allowed_origins or [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    origin_regex = settings.CORS_ALLOWED_ORIGIN_REGEX.strip() or None
    if origin_regex is None and settings.ENVIRONMENT != "production":
        origin_regex = _DEV_LAN_CORS_REGEX
    methods = settings.cors_allowed_methods or [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ]
    headers = settings.cors_allowed_headers or ["Authorization", "Content-Type", "X-API-Key"]
    allow_credentials = settings.CORS_ALLOW_CREDENTIALS

    if "*" in origins and allow_credentials:
        logger.warning(
            "CORS '*' cannot be combined with credentials; disabling credentials for CORS."
        )
        allow_credentials = False

    return origins, origin_regex, allow_credentials, methods, headers


cors_origins, cors_origin_regex, cors_allow_credentials, cors_methods, cors_headers = resolve_cors_middleware_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=cors_origin_regex,
    allow_credentials=cors_allow_credentials,
    allow_methods=cors_methods,
    allow_headers=cors_headers,
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": request_id},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", "unknown")
    errors = exc.errors()
    first_msg = errors[0].get("msg", "Validation error") if errors else "Validation error"
    # Pydantic v2 puts the original exception in `ctx["error"]` for
    # `model_validator`-raised ValueErrors. That object is not JSON-serialisable,
    # so coerce every value through `str()` before handing the payload to
    # JSONResponse.
    safe_errors = []
    for err in errors:
        cleaned = dict(err)
        ctx = cleaned.get("ctx")
        if isinstance(ctx, dict):
            cleaned["ctx"] = {k: str(v) for k, v in ctx.items()}
        safe_errors.append(cleaned)
    return JSONResponse(
        status_code=422,
        content={
            "detail": first_msg,
            "errors": safe_errors,
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "Unhandled exception path=%s: %s",
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )


_DEMO_MODE_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Allow auth endpoints in demo mode so users can still log in
_DEMO_MODE_WRITE_ALLOWLIST = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/refresh",
    "/health",
})


@app.middleware("http")
async def demo_mode_guard(request: Request, call_next):
    """Block all mutating requests when DEMO_MODE is True (8.8)."""
    if (
        settings.DEMO_MODE
        and request.method not in _DEMO_MODE_SAFE_METHODS
        and request.url.path not in _DEMO_MODE_WRITE_ALLOWLIST
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "This is a read-only demo environment. Write operations are disabled.",
                "demo_mode": True,
            },
        )
    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
    correlation_id = correlation_id_from_headers(request.headers, fallback=request_id)
    tenant_scope_token = set_current_tenant_scope(None)
    request.state.request_id = request_id
    request.state.correlation_id = correlation_id
    start = time.perf_counter()
    response = None
    try:
        with bind_request_context(
            request_id=request_id,
            correlation_id=correlation_id,
        ):
            response = await call_next(request)
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s -> %d (%dms)",
            request.method,
            request.url.path,
            response.status_code if response is not None else 500,
            int(duration_ms),
        )
        reset_current_tenant_scope(tenant_scope_token)
        if settings.ENABLE_QUERY_PROFILER:
            clear_request_query_counters(request_id)

    response.headers[REQUEST_ID_HEADER] = request_id
    response.headers[CORRELATION_ID_HEADER] = correlation_id
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Register routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(orders.router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(recurring_orders.router, prefix="/api/v1", tags=["Recurring Orders"])
app.include_router(couriers.router, prefix="/api/v1/couriers", tags=["Couriers"])
app.include_router(routes.router, prefix="/api/v1/routes", tags=["Routes"])
app.include_router(tracking.router, prefix="/api/v1/tracking", tags=["Tracking"])
app.include_router(dispatch.router, prefix="/api/v1", tags=["Dispatch"])
app.include_router(webhooks.router, prefix="/api/v1", tags=["Webhooks"])
app.include_router(api_keys.router, prefix="/api/v1", tags=["API Keys"])
app.include_router(constraints.router, prefix="/api/v1/constraints", tags=["Constraints"])
app.include_router(operations_settings.router, prefix="/api/v1", tags=["Operations Settings"])
app.include_router(operations_settings.features_router, prefix="/api/v1", tags=["Operations Settings"])
app.include_router(analytics.router, prefix="/api/v1", tags=["Analytics"])
app.include_router(geocode.router, prefix="/api/v1", tags=["Geocoding"])
app.include_router(depots.router, prefix="/api/v1/depots", tags=["Depots"])
app.include_router(depot_edges.router, prefix="/api/v1/depot-edges", tags=["Depot Edges"])
app.include_router(packages.router, prefix="/api/v1", tags=["Packages"])
app.include_router(sla_alerts.router, prefix="/api/v1", tags=["SLA Alerts"])
app.include_router(audit.router, prefix="/api/v1", tags=["Audit"])
app.include_router(admin.router, prefix="/api/v1", tags=["Superadmin"])
app.include_router(superadmin.router, prefix="/api/v1", tags=["Superadmin"])
app.include_router(traffic_priors.router, prefix="/api/v1", tags=["Traffic Priors"])
app.include_router(branding.router, prefix="/api/v1", tags=["Branding"])
app.include_router(oidc.router, prefix="/api/v1", tags=["OIDC / SSO"])
app.include_router(rbac.router, prefix="/api/v1", tags=["RBAC"])
app.include_router(vehicle_specs.router, prefix="/api/v1/vehicle-specs", tags=["Vehicle Specs"])
app.include_router(vehicle_tags.router, prefix="/api/v1/vehicle-tags", tags=["Vehicle Tags"])


async def startup_checks() -> None:
    """Fail fast on insecure config, unreachable DB, or stale schema."""
    strict_runtime = settings.ENVIRONMENT.lower() not in {
        "development",
        "dev",
        "local",
        "test",
        "testing",
        "ci",
    }

    # ── Production configuration guards ──────────────────────────────
    if not settings.JWT_SECRET or settings.JWT_SECRET == "dev-secret-change-in-production":
        raise RuntimeError(
            "JWT_SECRET is not set or is using the insecure default. "
            "Set a strong random secret via the JWT_SECRET environment variable."
        )

    if not settings.AUTH_JWT_ACTIVE_KID.strip():
        raise RuntimeError(
            "AUTH_JWT_ACTIVE_KID is empty. Set an explicit key id so auth token "
            "rotation can be performed safely."
        )

    if settings.AUTH_JWT_ACTIVE_KID in settings.AUTH_JWT_PREVIOUS_KEYS:
        raise RuntimeError(
            "AUTH_JWT_PREVIOUS_KEYS reuses AUTH_JWT_ACTIVE_KID. Remove the active kid "
            "from the previous-key ring before starting the app."
        )

    if not settings.TRACKING_JWT_SECRET:
        message = (
            "TRACKING_JWT_SECRET is not set — public tracking tokens will share the "
            "staff JWT signing secret. Set a dedicated tracking secret before going live."
        )
        if strict_runtime:
            raise RuntimeError(message)
        logger.warning(message)

    if not settings.TRACKING_JWT_ACTIVE_KID.strip():
        raise RuntimeError(
            "TRACKING_JWT_ACTIVE_KID is empty. Set an explicit key id so public "
            "tracking token rotation can be performed safely."
        )

    if settings.TRACKING_JWT_ACTIVE_KID in settings.TRACKING_JWT_PREVIOUS_KEYS:
        raise RuntimeError(
            "TRACKING_JWT_PREVIOUS_KEYS reuses TRACKING_JWT_ACTIVE_KID. Remove the active kid "
            "from the previous-key ring before starting the app."
        )
    elif settings.TRACKING_JWT_SECRET == settings.JWT_SECRET:
        message = (
            "TRACKING_JWT_SECRET matches JWT_SECRET — public tracking tokens are not "
            "cryptographically isolated from staff auth tokens. Use distinct secrets."
        )
        if strict_runtime:
            raise RuntimeError(message)
        logger.warning(message)

    if settings.S3_PRESIGN_DRY_RUN:
        message = (
            "S3_PRESIGN_DRY_RUN is True — POD photo/signature uploads will return "
            "fake URLs. Set S3_PRESIGN_DRY_RUN=False for production."
        )
        if strict_runtime:
            raise RuntimeError(message)
        logger.warning(message)

    if settings.NOTIFICATION_DRY_RUN:
        message = (
            "NOTIFICATION_DRY_RUN is True — no customer emails or SMS will be sent. "
            "Set NOTIFICATION_DRY_RUN=False for production."
        )
        if strict_runtime:
            raise RuntimeError(message)
        logger.warning(message)

    if not settings.WEBHOOK_SECRET_ENCRYPTION_KEY:
        message = (
            "WEBHOOK_SECRET_ENCRYPTION_KEY is not set — webhook and OIDC secrets "
            "will fall back to JWT_SECRET-derived encryption. Set a dedicated "
            "encryption key before going live."
        )
        if strict_runtime:
            raise RuntimeError(message)
        logger.warning(message)

    # ── Infra URL localhost guards (8.7) ─────────────────────────────
    _LOCALHOST_PATTERNS = ("localhost", "127.0.0.1", "::1")
    _infra_checks: list[tuple[str, str]] = [
        (settings.DATABASE_URL, "DATABASE_URL"),
        (settings.REDIS_URL, "REDIS_URL"),
        (settings.CELERY_BROKER_URL, "CELERY_BROKER_URL"),
        (settings.CELERY_RESULT_BACKEND, "CELERY_RESULT_BACKEND"),
    ]
    for url_value, var_name in _infra_checks:
        if any(pat in url_value for pat in _LOCALHOST_PATTERNS):
            message = (
                f"{var_name} appears to point at localhost ({url_value!r}). "
                "This is almost certainly a misconfiguration in production. "
                f"Set {var_name} to the correct production URL."
            )
            if strict_runtime:
                raise RuntimeError(message)
            logger.warning(message)

    # ── Database connectivity & migration checks ─────────────────────
    if settings.SKIP_DB_STARTUP_CHECKS:
        return

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        try:
            db_rev = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        except Exception as exc:
            raise RuntimeError(
                "Database is reachable but alembic_version is missing. "
                "Run `alembic upgrade head`."
            ) from exc

    root = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(root / "migrations"))
    script = ScriptDirectory.from_config(alembic_cfg)
    heads = set(script.get_heads())
    if db_rev not in heads:
        logger.info(
            "Database schema out of date (current=%s, expected=%s) — running migrations",
            db_rev, sorted(heads),
        )
        import asyncio
        await asyncio.to_thread(alembic_command.upgrade, alembic_cfg, "head")
        logger.info("Migrations applied successfully")


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "demo_mode": settings.DEMO_MODE}
