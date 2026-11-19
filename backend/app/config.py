import pathlib

from pydantic import Field
from pydantic_settings import BaseSettings


def _read_osrm_version_stamp() -> str | None:
    """Read the OSRM data version stamp written by prepare_osrm_data.sh."""
    for candidate in (
        pathlib.Path("osrm_data/.version"),       # when CWD is backend/
        pathlib.Path("/app/osrm_data/.version"),   # inside Docker container
    ):
        try:
            return candidate.read_text().strip()
        except OSError:
            continue
    return None


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://delivery:delivery@localhost:5432/delivery_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    AUTH_JWT_ISSUER: str = "lastmile-auth"
    AUTH_ACCESS_JWT_AUDIENCE: str = "lastmile-staff-api"
    AUTH_REFRESH_JWT_AUDIENCE: str = "lastmile-staff-refresh"
    AUTH_JWT_ACTIVE_KID: str = "auth-v1"
    AUTH_JWT_PREVIOUS_KEYS: dict[str, str] = Field(default_factory=dict)
    AUTH_SIGNUP_RATE_LIMIT_PER_MIN: int = 3
    TRACKING_JWT_SECRET: str | None = None
    TRACKING_JWT_ISSUER: str = "lastmile-tracking"
    TRACKING_JWT_AUDIENCE: str = "lastmile-public-tracking"
    TRACKING_JWT_ACTIVE_KID: str = "tracking-v1"
    TRACKING_JWT_PREVIOUS_KEYS: dict[str, str] = Field(default_factory=dict)
    PUBLIC_TRACKING_READ_RATE_LIMIT_PER_MIN: int = 60
    PUBLIC_TRACKING_WRITE_RATE_LIMIT_PER_HOUR: int = 6
    TRUST_PROXY_HEADERS: bool = False
    TRUSTED_PROXY_CIDRS: str = ""

    # Routing — self-hosted OSRM (local Docker service, no API key)
    OSRM_BASE_URL: str = "http://localhost:5000"
    OSRM_PROFILE_BASE_URLS: dict[str, str] = Field(default_factory=dict)
    OSRM_TIMEOUT_SECONDS: float = 10.0
    OSRM_MAX_TABLE_SIZE: int = 200

    # Photon Geocoding
    PHOTON_BASE_URL: str = "http://localhost:2322"

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Optimization Engine
    DISTANCE_MATRIX_CACHE_TTL: int = 3600  # 1 hour (local OSRM is fast)
    DISTANCE_MATRIX_CACHE_VERSION: str = _read_osrm_version_stamp() or "v2"
    MAX_STOPS_PER_ROUTE: int = 25
    BULK_MOTORCYCLE_MAX_STOP_COUNT: int = 10
    TIME_LIMIT_SECONDS: int = 60  # OR-Tools solver time limit
    FLEXIBLE_RELOAD_SOLVER_ENABLED: bool = True
    SERVICE_TIME_MINUTES: int = 5  # per-stop service time
    SPEED_ESTIMATE_KMH: float = 30.0  # for initial ETA estimates before matrix is available
    ETA_CALIBRATION_MIN_SAMPLES: int = 5
    ETA_MAX_HISTORICAL_DELAY_SECONDS: int = 900
    REOPTIMIZATION_LOCK_SECONDS: int = 120
    REOPTIMIZATION_COOLDOWN_SECONDS: int = 180
    PENDING_RETRY_INTERVAL_MINUTES: int = 10
    PENDING_RETRY_STALE_MINUTES: int = 10
    DRIVER_BREAK_DURATION_MINUTES: int = 30
    DRIVER_BREAK_EARLIEST_OFFSET_HOURS: float = 3.0
    DRIVER_BREAK_LATEST_OFFSET_HOURS: float = 6.0
    DRIVER_BREAK_MIN_SHIFT_HOURS: float = 5.0
    ENABLE_TRAFFIC_PROFILES: bool = True
    TRAFFIC_PROFILE_ENABLED: bool = True
    TIME_DEPENDENT_TRAVEL_ENABLED: bool = False
    TRAFFIC_PROFILE_MIN_SAMPLES: int = 5
    TRAFFIC_PROFILE_EMA_ALPHA: float = 0.35
    # Bucketing timezone. Rush hour is a local concept; keep this aligned with
    # the operational region so day_of_week/hour_bucket match real driver days.
    # The Istanbul pilot uses Europe/Istanbul (UTC+3, no DST since 2016).
    TRAFFIC_PROFILE_TIMEZONE: str = "Europe/Istanbul"
    TRAFFIC_PROFILE_MIN_MULTIPLIER: float = 0.6
    TRAFFIC_PROFILE_MAX_MULTIPLIER: float = 1.8
    TRAFFIC_PROFILE_CACHE_TTL_SECONDS: int = 900
    # Historical traffic prior (IBB municipal open data backfill).
    TRAFFIC_PRIOR_ENABLED: bool = True
    # TODO(region-per-tenant): replace with per-tenant/per-depot resolution.
    TRAFFIC_PRIOR_ACTIVE_REGION_CODE: str = "IST"
    TRAFFIC_PRIOR_MIN_BUCKET_SAMPLES: int = 10
    TRAFFIC_PRIOR_MIN_CELL_ROWS: int = 50
    ROUTE_GEOMETRY_CACHE_TTL_SECONDS: int = 180

    # S3 (Phase 3+)
    S3_BUCKET: str = "delivery-pod-files"
    AWS_REGION: str = "us-east-1"
    POD_UPLOAD_URL_EXPIRES_SECONDS: int = 900
    S3_PRESIGN_DRY_RUN: bool = True

    # Rate limiting
    AUTH_RATE_LIMIT_PER_MIN: int = 5
    API_KEY_RATE_LIMIT_PER_MIN: int = 60
    REDIS_FALLBACK_RETRY_SECONDS: int = 5

    # CORS
    CORS_ALLOWED_ORIGINS: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://localhost:5174,"
        "http://127.0.0.1:5174,"
        "http://localhost:5175,"
        "http://127.0.0.1:5175,"
        "http://localhost:19006,"
        "http://127.0.0.1:19006"
    )
    CORS_ALLOWED_METHODS: str = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    CORS_ALLOWED_HEADERS: str = "Authorization,Content-Type,X-API-Key"
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOWED_ORIGIN_REGEX: str = ""

    # Geofencing
    ARRIVAL_RADIUS_METERS: float = 100.0
    ARRIVAL_RADIUS_ON_DEMAND_METERS: float = 100.0
    ARRIVAL_RADIUS_SCHEDULED_METERS: float = 100.0
    ARRIVAL_RADIUS_BULK_METERS: float = 150.0
    ARRIVAL_RADIUS_REVERSE_METERS: float = 100.0
    GEOFENCE_DWELL_TIMEOUT_SECONDS: int = 300
    SKIP_DB_STARTUP_CHECKS: bool = False
    ENABLE_MAPLIBRE_DISPATCHER: bool = True

    # Runtime metrics export
    EMF_METRICS_ENABLED: bool = False
    EMF_METRICS_NAMESPACE: str = "LastMile/Application"
    EMF_METRICS_APPLICATION: str = "last-mile-delivery"
    EMF_METRICS_SERVICE: str = "api"

    # Phase 2.3 — Open Charge Map (charging-station catalogue) sync.
    # Free-tier API key from openchargemap.io; without it the weekly
    # sync task no-ops with a warning (dispatch then falls back to
    # whatever's already in the local `charging_stations` table —
    # OCM is NOT a runtime dependency, only a catalogue source).
    # Weekly cadence (see celery_app beat schedule); ~5000 Turkey
    # stations in one call is well under the OCM free-tier rate limit.
    OPEN_CHARGE_MAP_API_KEY: str = ""
    OPEN_CHARGE_MAP_BASE_URL: str = "https://api.openchargemap.io/v3"
    OPEN_CHARGE_MAP_COUNTRY_CODE: str = "TR"
    OPEN_CHARGE_MAP_SYNC_PAGE_SIZE: int = 10000
    # Stations not seen in this many days are auto-marked is_active=False.
    # Sized to comfortably exceed the 7-day sync cadence × ~12 weeks of
    # downtime tolerance — avoids retiring real stations because OCM
    # was flaky for a stretch.
    OPEN_CHARGE_MAP_STALE_DAYS: int = 90

    # Webhooks
    WEBHOOK_MAX_ATTEMPTS: int = 5
    WEBHOOK_BACKOFF_BASE_SECONDS: int = 60
    WEBHOOK_BACKOFF_MAX_SECONDS: int = 3600
    WEBHOOK_SECRET_ENCRYPTION_KEY: str | None = None
    OIDC_SECRET_ENCRYPTION_KEY: str | None = None
    OIDC_STATE_TTL_SECONDS: int = 600

    # Notifications
    NOTIFICATION_DRY_RUN: bool = True
    NOTIFICATION_DEDUPE_TTL_SECONDS: int = 300
    TRACKING_PUBLIC_BASE_URL: str = "http://localhost:5174/tracking"
    FRONTEND_URL: str = "http://localhost:5173"
    SES_FROM_EMAIL: str | None = None
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    TWILIO_FROM_NUMBER: str | None = None
    # Push notifications — Firebase Cloud Messaging HTTP v1
    # FCM_PROJECT_ID: your Firebase project ID (visible in the Firebase Console)
    # FCM_SERVICE_ACCOUNT_JSON: the full JSON content of your Firebase service account key file.
    #   Generate at: Firebase Console → Project Settings → Service Accounts → Generate new private key.
    #   Store the entire JSON as a single-line string in this variable (or use a secrets manager).
    FCM_PROJECT_ID: str | None = None
    FCM_SERVICE_ACCOUNT_JSON: str | None = None
    EXPO_PUSH_ACCESS_TOKEN: str | None = None

    # Dispatch planning horizon — batch optimizer only picks up orders whose
    # earliest_delivery falls within this window from now. Orders further out
    # remain PENDING until a later batch run. Default 36 h covers same-day +
    # overnight orders scheduled for the following morning.
    DISPATCH_PLANNING_HORIZON_HOURS: int = 36

    # Failed delivery escalation
    FAILED_ORDER_RESPONSE_WINDOW_HOURS: int = 48

    # Sentry
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    ENVIRONMENT: str = "development"

    # Data retention
    LOCATION_RETENTION_DAYS: int = 90
    WEBHOOK_LOG_RETENTION_DAYS: int = 90
    NOTIFICATION_RETENTION_DAYS: int = 90
    AUDIT_LOG_RETENTION_DAYS: int = 365

    # Superadmin (platform-level management — keep this secret)
    SUPERADMIN_API_KEY: str | None = None

    # Email verification (8.6)
    # Set True to require users to verify their email before login is allowed
    # (after a grace period). False = verification is sent but not enforced.
    REQUIRE_EMAIL_VERIFICATION: bool = False

    # Demo / Sandbox mode (8.8)
    # When True, all write operations are blocked at the API layer (read-only sandbox).
    # This lets prospects explore the UI with pre-loaded seed data without modifying anything.
    DEMO_MODE: bool = False

    # Performance audit / query profiler (8.10)
    # When True, slow queries and potential N+1 patterns are logged as warnings.
    # Leave False in production unless actively investigating performance issues.
    ENABLE_QUERY_PROFILER: bool = False
    SLOW_QUERY_THRESHOLD_MS: float = 200.0
    N1_DETECTION_THRESHOLD: int = 10

    model_config = {"env_file": ".env", "extra": "ignore"}

    @staticmethod
    def _parse_csv(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

    @property
    def cors_allowed_origins(self) -> list[str]:
        return self._parse_csv(self.CORS_ALLOWED_ORIGINS)

    @property
    def cors_allowed_methods(self) -> list[str]:
        return self._parse_csv(self.CORS_ALLOWED_METHODS)

    @property
    def cors_allowed_headers(self) -> list[str]:
        return self._parse_csv(self.CORS_ALLOWED_HEADERS)

    @property
    def trusted_proxy_cidrs(self) -> list[str]:
        return self._parse_csv(self.TRUSTED_PROXY_CIDRS)


settings = Settings()
