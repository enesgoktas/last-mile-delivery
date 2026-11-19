from app.workers.celery_app import celery_app


def test_celery_routes_include_retention_tasks():
    routes = celery_app.conf.task_routes or {}
    assert "app.workers.retention_tasks.*" in routes
    assert routes["app.workers.retention_tasks.*"]["queue"] == "operations"


def test_celery_beat_schedule_includes_retention_cleanup():
    beat_schedule = celery_app.conf.beat_schedule or {}
    assert "data-retention-cleanup-daily" in beat_schedule
    entry = beat_schedule["data-retention-cleanup-daily"]
    assert entry["task"] == "app.workers.retention_tasks.run_data_retention_cleanup"
