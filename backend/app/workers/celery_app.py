"""
Celery application factory.

Broker and result backend: Redis (DB 1, separate from location cache on DB 0).
Task serialiser: JSON (no pickle for security).
Acks late: True — tasks are re-queued on unexpected worker crash.
"""
from celery import Celery

try:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]
    CeleryIntegration = None  # type: ignore[misc,assignment]

from app.config import settings

if sentry_sdk is not None and settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        integrations=[CeleryIntegration()],
        send_default_pii=False,
    )

celery_app = Celery(
    "delivery_workers",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_concurrency=2,
    beat_scheduler="redbeat.RedBeatScheduler",
    redbeat_redis_url=settings.REDIS_URL,
    # Auto-discover tasks in our workers package
    task_routes={
        "app.workers.optimization_tasks.*": {"queue": "optimization"},
        "app.workers.energy_price_tasks.*": {"queue": "operations"},
        "app.workers.charging_station_tasks.*": {"queue": "operations"},
        "app.workers.webhook_tasks.*": {"queue": "integrations"},
        "app.workers.notification_tasks.*": {"queue": "notifications"},
        "app.workers.geofencing_tasks.*": {"queue": "geofencing"},
        "app.workers.failed_delivery_tasks.*": {"queue": "operations"},
        "app.workers.retention_tasks.*": {"queue": "operations"},
        "app.workers.sla_tasks.*": {"queue": "operations"},
        "app.workers.recurring_order_tasks.*": {"queue": "operations"},
        "app.workers.traffic_prior_tasks.*": {"queue": "operations"},
    },
    imports=(
        "app.workers.optimization_tasks",
        "app.workers.energy_price_tasks",
        "app.workers.charging_station_tasks",
        "app.workers.webhook_tasks",
        "app.workers.notification_tasks",
        "app.workers.geofencing_tasks",
        "app.workers.failed_delivery_tasks",
        "app.workers.retention_tasks",
        "app.workers.sla_tasks",
        "app.workers.recurring_order_tasks",
        "app.workers.traffic_prior_tasks",
    ),
    beat_schedule={
        # Automatic batch optimization is intentionally disabled.
        # Dispatchers trigger assignment manually via the "Assign Optimally"
        # button, scoped to a specific date. Auto-running this would silently
        # assign orders across the 36-hour planning horizon without consent.
        #
        # "scheduled-batch-optimization-hourly": {
        #     "task": "app.workers.optimization_tasks.run_batch_optimization",
        #     "schedule": 60 * 60,
        # },
        "failed-delivery-escalation-check": {
            "task": "app.workers.failed_delivery_tasks.escalate_stale_failed_orders",
            "schedule": 15 * 60,  # every 15 minutes
        },
        "data-retention-cleanup-daily": {
            "task": "app.workers.retention_tasks.run_data_retention_cleanup",
            "schedule": 24 * 60 * 60,  # every 24 hours
        },
        "turkey-energy-prices-refresh-daily": {
            "task": "app.workers.energy_price_tasks.refresh_turkey_energy_prices",
            "schedule": 24 * 60 * 60,  # every 24 hours
        },
        "open-charge-map-refresh-weekly": {
            "task": "app.workers.charging_station_tasks.refresh_charging_stations",
            # Weekly cadence — charging-station infrastructure changes
            # slowly (new stations added monthly at most), and we don't
            # want OCM to be a hard runtime dependency. Dispatch reads
            # only from our local `charging_stations` table; this task
            # just keeps the catalogue fresh.
            "schedule": 7 * 24 * 60 * 60,  # every 7 days
        },
        # Disabled: greedy one-by-one insertion conflicts with batch VRP optimization.
        # Assignment is handled exclusively via the "Assign Optimally" button.
        # "retry-stale-pending-orders": {
        #     "task": "app.workers.optimization_tasks.retry_stale_pending_orders",
        #     "schedule": settings.PENDING_RETRY_INTERVAL_MINUTES * 60,
        # },
        "sla-violation-scan": {
            "task": "app.workers.sla_tasks.scan_sla_violations",
            "schedule": 5 * 60,  # every 5 minutes
        },
        "schedule-drift-check": {
            "task": "app.workers.sla_tasks.check_schedule_drift",
            "schedule": 10 * 60,  # every 10 minutes
        },
        "expire-overdue-orders": {
            "task": "app.workers.sla_tasks.expire_overdue_orders",
            "schedule": 10 * 60,  # every 10 minutes
        },
        # Recurring order templates: materialise due occurrences hourly.
        # Service-layer logic is idempotent (unique (template, occurrence_date))
        # so the cadence only affects latency, not correctness.
        "materialise-recurring-orders-hourly": {
            "task": "app.workers.recurring_order_tasks.materialise_recurring_orders",
            "schedule": 60 * 60,
        },
        # IBB monthly traffic prior refresh. ~30-day cadence is enough — IBB
        # publishes new monthly bundles roughly at the start of each calendar
        # month; running every ~30 days keeps the prior fresh without
        # overworking the public endpoint. Task is idempotent.
        "traffic-priors-refresh-monthly": {
            "task": "app.workers.traffic_prior_tasks.refresh_traffic_priors",
            "schedule": 30 * 24 * 60 * 60,  # every 30 days
        },
    },
)

# Import tasks so they are registered with the app
celery_app.autodiscover_tasks(["app.workers"])
