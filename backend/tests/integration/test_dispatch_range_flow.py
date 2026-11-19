"""API-layer tests for the range bulk dispatch endpoint (`/assign-range`).

The legacy `/assign-week` URL is exercised via the alias tests at the bottom
of the file — same handler, same response shape, just an older URL.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

from app.api import dispatch as dispatch_api
from tests.integration.test_utils import build_test_client, find_route_dependency


def _make_role_override(tenant_id: uuid.UUID):
    async def _override_role():
        return SimpleNamespace(
            id=uuid.uuid4(), tenant_id=tenant_id, role="DISPATCHER", is_active=True
        )

    return _override_role


def _patch_active_optimization_lock(monkeypatch):
    """Bypass the global single-flight optimization lock for these tests.

    The lock lives in shared Redis state. Across multiple successful POSTs
    in one test file the second call would otherwise see "another tenant
    is running" — which is correct production behavior but not what these
    handler-shape tests are validating.
    """
    from app.services import optimization_control_service as svc

    class _NoCancel:
        cancelled = False
        blocked = False
        detail = None
        task_id = None

    async def _no_supersede(db, *, tenant_id):  # noqa: ANN001
        return _NoCancel()

    def _no_record(**_kwargs):
        return None

    monkeypatch.setattr(
        svc, "supersede_active_batch_optimization_for_tenant", _no_supersede
    )
    monkeypatch.setattr(svc, "record_active_batch_optimization_task", _no_record)


def test_assign_range_enqueues_range_batch_task(monkeypatch):
    _patch_active_optimization_lock(monkeypatch)
    role_dep = find_route_dependency(
        "/api/v1/dispatch/assign-range", "current_user"
    )
    tenant_id = uuid.uuid4()

    class _MockTask:
        id = "task_range_42"

    captured: dict = {}

    def _capture_enqueue(_task, *, kwargs=None, **_extra):
        captured["task"] = _task
        captured["kwargs"] = kwargs
        return _MockTask()

    monkeypatch.setattr(dispatch_api, "enqueue_task", _capture_enqueue)

    today = date(2026, 4, 28)
    end = today + timedelta(days=4)
    with build_test_client(
        dependency_overrides={role_dep: _make_role_override(tenant_id)}
    ) as client:
        resp = client.post(
            f"/api/v1/dispatch/assign-range?date_from={today.isoformat()}&date_to={end.isoformat()}",
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["task_id"] == "task_range_42"
    assert body["span_days"] == 5
    assert "Range planner enqueued" in body["message"]
    assert captured["kwargs"]["tenant_id"] == str(tenant_id)
    assert captured["kwargs"]["draft"] is False
    # date_to_iso is exclusive (inclusive end + 1 day).
    assert captured["kwargs"]["date_from_iso"].startswith("2026-04-28")
    assert captured["kwargs"]["date_to_iso"].startswith("2026-05-03")


def test_assign_range_rejects_inverted_range(monkeypatch):
    role_dep = find_route_dependency(
        "/api/v1/dispatch/assign-range", "current_user"
    )
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(
        dispatch_api, "enqueue_task", lambda *a, **kw: SimpleNamespace(id="x")
    )

    with build_test_client(
        dependency_overrides={role_dep: _make_role_override(tenant_id)}
    ) as client:
        resp = client.post(
            "/api/v1/dispatch/assign-range?date_from=2026-04-30&date_to=2026-04-28"
        )
    assert resp.status_code == 422


def test_assign_range_accepts_a_30_day_span(monkeypatch):
    """The 14-day cap is gone. A 30-day span must enqueue cleanly."""
    _patch_active_optimization_lock(monkeypatch)
    role_dep = find_route_dependency(
        "/api/v1/dispatch/assign-range", "current_user"
    )
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(
        dispatch_api, "enqueue_task", lambda *a, **kw: SimpleNamespace(id="task_30d")
    )

    with build_test_client(
        dependency_overrides={role_dep: _make_role_override(tenant_id)}
    ) as client:
        resp = client.post(
            "/api/v1/dispatch/assign-range?date_from=2026-04-01&date_to=2026-04-30"
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["span_days"] == 30


def test_assign_range_accepts_a_180_day_span(monkeypatch):
    """The safety net allows up to 180 days inclusive."""
    _patch_active_optimization_lock(monkeypatch)
    role_dep = find_route_dependency(
        "/api/v1/dispatch/assign-range", "current_user"
    )
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(
        dispatch_api, "enqueue_task", lambda *a, **kw: SimpleNamespace(id="task_180d")
    )

    with build_test_client(
        dependency_overrides={role_dep: _make_role_override(tenant_id)}
    ) as client:
        resp = client.post(
            "/api/v1/dispatch/assign-range?date_from=2026-01-01&date_to=2026-06-29"
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["span_days"] == 180


def test_assign_range_rejects_spans_past_180_days(monkeypatch):
    """A typo like 'year 9999' (or any span >180 days) is rejected as a
    sanity guard. The wording must point the user at smaller windows."""
    role_dep = find_route_dependency(
        "/api/v1/dispatch/assign-range", "current_user"
    )
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(
        dispatch_api, "enqueue_task", lambda *a, **kw: SimpleNamespace(id="x")
    )

    with build_test_client(
        dependency_overrides={role_dep: _make_role_override(tenant_id)}
    ) as client:
        resp = client.post(
            "/api/v1/dispatch/assign-range?date_from=2026-01-01&date_to=2027-01-01"
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"].lower()
    assert "180" in detail
    assert "split" in detail or "smaller" in detail


# ── Back-compat: /assign-week alias ───────────────────────────────────────


def test_assign_range_draft_param_threads_through(monkeypatch):
    """When `draft=true` is on the query string, the worker kwargs must
    carry `draft: True` so the service creates DRAFT routes and leaves
    orders in PENDING."""
    _patch_active_optimization_lock(monkeypatch)
    role_dep = find_route_dependency(
        "/api/v1/dispatch/assign-range", "current_user"
    )
    tenant_id = uuid.uuid4()

    class _MockTask:
        id = "task_preview"

    captured: dict = {}

    def _capture_enqueue(_task, *, kwargs=None, **_extra):
        captured["kwargs"] = kwargs
        return _MockTask()

    monkeypatch.setattr(dispatch_api, "enqueue_task", _capture_enqueue)

    with build_test_client(
        dependency_overrides={role_dep: _make_role_override(tenant_id)}
    ) as client:
        resp = client.post(
            "/api/v1/dispatch/assign-range"
            "?date_from=2026-04-28&date_to=2026-05-02&draft=true"
        )
    assert resp.status_code == 202, resp.text
    assert captured["kwargs"]["draft"] is True
    # The default (no query param) must still be False — back-compat.
    captured.clear()
    with build_test_client(
        dependency_overrides={role_dep: _make_role_override(tenant_id)}
    ) as client:
        resp = client.post(
            "/api/v1/dispatch/assign-range?date_from=2026-04-28&date_to=2026-05-02"
        )
    assert resp.status_code == 202, resp.text
    assert captured["kwargs"]["draft"] is False
    # And the preview message labels itself accordingly.
    assert "Range planner enqueued (commit)" in resp.json()["message"]


def test_assign_week_alias_still_works(monkeypatch):
    """The deprecated `/assign-week` URL routes to the same handler and
    produces the same response. Frontend back-compat depends on this."""
    _patch_active_optimization_lock(monkeypatch)
    role_dep = find_route_dependency(
        "/api/v1/dispatch/assign-week", "current_user"
    )
    tenant_id = uuid.uuid4()

    class _MockTask:
        id = "task_alias"

    captured: dict = {}

    def _capture_enqueue(_task, *, kwargs=None, **_extra):
        captured["kwargs"] = kwargs
        return _MockTask()

    monkeypatch.setattr(dispatch_api, "enqueue_task", _capture_enqueue)

    with build_test_client(
        dependency_overrides={role_dep: _make_role_override(tenant_id)}
    ) as client:
        resp = client.post(
            "/api/v1/dispatch/assign-week?date_from=2026-04-28&date_to=2026-05-02"
        )

    assert resp.status_code == 202, resp.text
    assert resp.json()["span_days"] == 5
    assert captured["kwargs"]["tenant_id"] == str(tenant_id)
