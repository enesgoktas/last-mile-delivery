from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import redis.asyncio as aioredis
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app import database
from app import main as main_module
from app.api import auth as auth_api
from app.api import oidc as oidc_api
from app.config import settings
from app.models import Base  # noqa: F401
from app.utils import auth as auth_utils
from app.utils import rate_limit

_APP_REDIS_DB_INDEX = 14
_CELERY_REDIS_DB_INDEX = 15
_TEST_SCHEMA_NAME = "live_integration"
_TEST_JWT_SECRET = "integration-test-secret-32-bytes!!"
_TEST_TRACKING_JWT_SECRET = "integration-test-tracking-secret-32b"


@dataclass(frozen=True)
class LiveRuntimeConfig:
    database_url: str
    schema_name: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str


@dataclass
class EngineBundle:
    engine: AsyncEngine
    async_session: async_sessionmaker[AsyncSession]
    celery_engine: AsyncEngine
    celery_async_session: async_sessionmaker[AsyncSession]


@dataclass
class RuntimeSnapshot:
    database_url: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str
    jwt_secret: str
    auth_jwt_active_kid: str
    auth_jwt_previous_keys: dict[str, str]
    tracking_jwt_secret: str | None
    tracking_jwt_active_kid: str
    tracking_jwt_previous_keys: dict[str, str]
    notification_dry_run: bool
    require_email_verification: bool
    environment: str
    skip_db_startup_checks: bool
    auth_rate_limit_per_min: int
    auth_signup_rate_limit_per_min: int
    public_tracking_read_rate_limit_per_min: int
    public_tracking_write_rate_limit_per_hour: int
    trust_proxy_headers: bool
    trusted_proxy_cidrs: str
    engine: AsyncEngine
    async_session: async_sessionmaker[AsyncSession]
    celery_engine: AsyncEngine
    celery_async_session: async_sessionmaker[AsyncSession]
    main_engine: AsyncEngine


def run_async(awaitable):
    return asyncio.run(awaitable)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _build_test_redis_url(base_url: str, db_index: int) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{db_index}", parsed.query, parsed.fragment))


def _build_live_runtime_config() -> LiveRuntimeConfig:
    return LiveRuntimeConfig(
        database_url=settings.DATABASE_URL,
        schema_name=_TEST_SCHEMA_NAME,
        redis_url=_build_test_redis_url(settings.REDIS_URL, _APP_REDIS_DB_INDEX),
        celery_broker_url=_build_test_redis_url(settings.CELERY_BROKER_URL, _CELERY_REDIS_DB_INDEX),
        celery_result_backend=_build_test_redis_url(settings.CELERY_RESULT_BACKEND, _CELERY_REDIS_DB_INDEX),
    )


def _alembic_head_revision() -> str:
    backend_root = Path(__file__).resolve().parents[2]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "migrations"))
    return ScriptDirectory.from_config(alembic_config).get_current_head()


async def _ensure_test_schema_exists(database_url: str, schema_name: str) -> None:
    admin_engine = create_async_engine(database_url)
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(schema_name)}"))
    finally:
        await admin_engine.dispose()


async def _ensure_database_ready(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        await conn.execute(text("SELECT version_num FROM alembic_version"))


async def _ensure_redis_ready(*urls: str) -> None:
    for url in dict.fromkeys(urls):
        client = aioredis.from_url(url, decode_responses=True)
        try:
            await client.ping()
        finally:
            await client.aclose()


def _make_engine_bundle(database_url: str, schema_name: str) -> EngineBundle:
    connect_args = {"server_settings": {"search_path": schema_name}}
    engine = create_async_engine(
        database_url,
        echo=False,
        poolclass=NullPool,
        connect_args=connect_args,
    )
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    celery_engine = create_async_engine(
        database_url,
        echo=False,
        poolclass=NullPool,
        connect_args=connect_args,
    )
    celery_async_session = async_sessionmaker(
        celery_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return EngineBundle(
        engine=engine,
        async_session=async_session,
        celery_engine=celery_engine,
        celery_async_session=celery_async_session,
    )


def _snapshot_runtime() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        database_url=settings.DATABASE_URL,
        redis_url=settings.REDIS_URL,
        celery_broker_url=settings.CELERY_BROKER_URL,
        celery_result_backend=settings.CELERY_RESULT_BACKEND,
        jwt_secret=settings.JWT_SECRET,
        auth_jwt_active_kid=settings.AUTH_JWT_ACTIVE_KID,
        auth_jwt_previous_keys=dict(settings.AUTH_JWT_PREVIOUS_KEYS),
        tracking_jwt_secret=settings.TRACKING_JWT_SECRET,
        tracking_jwt_active_kid=settings.TRACKING_JWT_ACTIVE_KID,
        tracking_jwt_previous_keys=dict(settings.TRACKING_JWT_PREVIOUS_KEYS),
        notification_dry_run=settings.NOTIFICATION_DRY_RUN,
        require_email_verification=settings.REQUIRE_EMAIL_VERIFICATION,
        environment=settings.ENVIRONMENT,
        skip_db_startup_checks=settings.SKIP_DB_STARTUP_CHECKS,
        auth_rate_limit_per_min=settings.AUTH_RATE_LIMIT_PER_MIN,
        auth_signup_rate_limit_per_min=settings.AUTH_SIGNUP_RATE_LIMIT_PER_MIN,
        public_tracking_read_rate_limit_per_min=settings.PUBLIC_TRACKING_READ_RATE_LIMIT_PER_MIN,
        public_tracking_write_rate_limit_per_hour=settings.PUBLIC_TRACKING_WRITE_RATE_LIMIT_PER_HOUR,
        trust_proxy_headers=settings.TRUST_PROXY_HEADERS,
        trusted_proxy_cidrs=settings.TRUSTED_PROXY_CIDRS,
        engine=database.engine,
        async_session=database.async_session,
        celery_engine=database.celery_engine,
        celery_async_session=database.celery_async_session,
        main_engine=main_module.engine,
    )


def _apply_live_runtime(runtime: LiveRuntimeConfig, bundle: EngineBundle) -> None:
    settings.DATABASE_URL = runtime.database_url
    settings.REDIS_URL = runtime.redis_url
    settings.CELERY_BROKER_URL = runtime.celery_broker_url
    settings.CELERY_RESULT_BACKEND = runtime.celery_result_backend
    settings.JWT_SECRET = _TEST_JWT_SECRET
    settings.AUTH_JWT_ACTIVE_KID = "test-auth-v1"
    settings.AUTH_JWT_PREVIOUS_KEYS = {}
    settings.TRACKING_JWT_SECRET = _TEST_TRACKING_JWT_SECRET
    settings.TRACKING_JWT_ACTIVE_KID = "test-tracking-v1"
    settings.TRACKING_JWT_PREVIOUS_KEYS = {}
    settings.NOTIFICATION_DRY_RUN = True
    settings.REQUIRE_EMAIL_VERIFICATION = False
    settings.ENVIRONMENT = "test"
    settings.SKIP_DB_STARTUP_CHECKS = False
    settings.AUTH_RATE_LIMIT_PER_MIN = 100
    settings.AUTH_SIGNUP_RATE_LIMIT_PER_MIN = 100
    settings.PUBLIC_TRACKING_READ_RATE_LIMIT_PER_MIN = 1000
    settings.PUBLIC_TRACKING_WRITE_RATE_LIMIT_PER_HOUR = 100
    settings.TRUST_PROXY_HEADERS = False
    settings.TRUSTED_PROXY_CIDRS = ""

    database.engine = bundle.engine
    database.async_session = bundle.async_session
    database.celery_engine = bundle.celery_engine
    database.celery_async_session = bundle.celery_async_session
    main_module.engine = bundle.engine


def _restore_runtime(snapshot: RuntimeSnapshot) -> None:
    settings.DATABASE_URL = snapshot.database_url
    settings.REDIS_URL = snapshot.redis_url
    settings.CELERY_BROKER_URL = snapshot.celery_broker_url
    settings.CELERY_RESULT_BACKEND = snapshot.celery_result_backend
    settings.JWT_SECRET = snapshot.jwt_secret
    settings.AUTH_JWT_ACTIVE_KID = snapshot.auth_jwt_active_kid
    settings.AUTH_JWT_PREVIOUS_KEYS = dict(snapshot.auth_jwt_previous_keys)
    settings.TRACKING_JWT_SECRET = snapshot.tracking_jwt_secret
    settings.TRACKING_JWT_ACTIVE_KID = snapshot.tracking_jwt_active_kid
    settings.TRACKING_JWT_PREVIOUS_KEYS = dict(snapshot.tracking_jwt_previous_keys)
    settings.NOTIFICATION_DRY_RUN = snapshot.notification_dry_run
    settings.REQUIRE_EMAIL_VERIFICATION = snapshot.require_email_verification
    settings.ENVIRONMENT = snapshot.environment
    settings.SKIP_DB_STARTUP_CHECKS = snapshot.skip_db_startup_checks
    settings.AUTH_RATE_LIMIT_PER_MIN = snapshot.auth_rate_limit_per_min
    settings.AUTH_SIGNUP_RATE_LIMIT_PER_MIN = snapshot.auth_signup_rate_limit_per_min
    settings.PUBLIC_TRACKING_READ_RATE_LIMIT_PER_MIN = snapshot.public_tracking_read_rate_limit_per_min
    settings.PUBLIC_TRACKING_WRITE_RATE_LIMIT_PER_HOUR = snapshot.public_tracking_write_rate_limit_per_hour
    settings.TRUST_PROXY_HEADERS = snapshot.trust_proxy_headers
    settings.TRUSTED_PROXY_CIDRS = snapshot.trusted_proxy_cidrs

    database.engine = snapshot.engine
    database.async_session = snapshot.async_session
    database.celery_engine = snapshot.celery_engine
    database.celery_async_session = snapshot.celery_async_session
    main_module.engine = snapshot.main_engine


async def _close_cached_redis_clients() -> None:
    redis_globals = (
        (auth_utils, "_redis"),
        (auth_api, "_pw_reset_redis"),
        (oidc_api, "_state_redis"),
        (rate_limit, "_redis"),
    )
    for module, attr_name in redis_globals:
        client = getattr(module, attr_name, None)
        if client is None:
            continue
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close is not None:
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except RuntimeError as exc:
                if "Event loop is closed" not in str(exc):
                    raise
        setattr(module, attr_name, None)

    rate_limit._fallback_windows.clear()
    rate_limit._redis_fallback_active = False
    rate_limit._redis_retry_after_ts = 0.0


async def _prepare_test_schema(engine: AsyncEngine, schema_name: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(schema_name)}"))
        await conn.execute(text(f"SET search_path TO {_quote_identifier(schema_name)}"))
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        # This live harness validates real Postgres/Redis request paths while the
        # main CI migration smoke check still validates Alembic against the
        # normal backend database.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version_num)"),
            {"version_num": _alembic_head_revision()},
        )


async def _reset_database(engine: AsyncEngine, schema_name: str) -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = :schema_name AND tablename <> 'alembic_version'
                ORDER BY tablename
                """
            ),
            {"schema_name": schema_name},
        )
        table_names = [row[0] for row in result]
        if not table_names:
            return
        qualified_table_names = ", ".join(
            f"{_quote_identifier(schema_name)}.{_quote_identifier(name)}"
            for name in table_names
        )
        await conn.execute(text(f"TRUNCATE TABLE {qualified_table_names} RESTART IDENTITY CASCADE"))


async def _reset_redis(*urls: str) -> None:
    for url in dict.fromkeys(urls):
        client = aioredis.from_url(url, decode_responses=True)
        try:
            await client.flushdb()
        finally:
            await client.aclose()


async def _reset_test_state(runtime: LiveRuntimeConfig, bundle: EngineBundle) -> None:
    await _close_cached_redis_clients()
    await _reset_redis(
        runtime.redis_url,
        runtime.celery_broker_url,
        runtime.celery_result_backend,
    )
    await _reset_database(bundle.engine, runtime.schema_name)


@pytest.fixture(scope="session")
def live_runtime_config():
    runtime = _build_live_runtime_config()
    try:
        run_async(_ensure_test_schema_exists(runtime.database_url, runtime.schema_name))
        engine_bundle = _make_engine_bundle(runtime.database_url, runtime.schema_name)
        try:
            run_async(_prepare_test_schema(engine_bundle.engine, runtime.schema_name))
            run_async(_ensure_database_ready(engine_bundle.engine))
        finally:
            run_async(engine_bundle.engine.dispose())
            run_async(engine_bundle.celery_engine.dispose())
        run_async(
            _ensure_redis_ready(
                runtime.redis_url,
                runtime.celery_broker_url,
                runtime.celery_result_backend,
            )
        )
    except Exception as exc:  # pragma: no cover - exercised only when infra is unavailable
        pytest.skip(f"Live integration tests require dedicated Postgres/Redis infra: {exc}")
    return runtime


@pytest.fixture
def live_test_client(live_runtime_config: LiveRuntimeConfig):
    snapshot = _snapshot_runtime()
    bundle = _make_engine_bundle(live_runtime_config.database_url, live_runtime_config.schema_name)
    _apply_live_runtime(live_runtime_config, bundle)
    run_async(_reset_test_state(live_runtime_config, bundle))
    main_module.app.dependency_overrides.clear()

    try:
        with TestClient(main_module.app) as client:
            yield client
    finally:
        main_module.app.dependency_overrides.clear()
        run_async(_reset_test_state(live_runtime_config, bundle))
        run_async(bundle.engine.dispose())
        run_async(bundle.celery_engine.dispose())
        _restore_runtime(snapshot)
