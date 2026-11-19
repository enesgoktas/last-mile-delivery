"""End-to-end live tests for the recurring-orders API and materialiser.

These tests exercise the real database and the real materialisation service
against a freshly-signed-up tenant so they double as a smoke test for the
Alembic migration and the celery worker entry-point.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.database import get_async_session_factory
from app.models.enums import OrderStatus, OrderType
from app.models.order import Order
from app.models.recurring_order import (
    RecurrenceCadence,
    RecurringOrderOccurrence,
    RecurringOrderTemplate,
)
from app.services import recurring_order_service
from app.workers import recurring_order_tasks
from tests.integration.live_api_helpers import auth_headers, signup_owner
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


def _get_me(client, *, access_token: str) -> dict:
    response = client.get(
        "/api/v1/auth/me",
        headers=auth_headers(access_token),
    )
    assert response.status_code == 200
    return response.json()


def _template_payload(name: str, **overrides) -> dict:
    base = {
        "name": name,
        "description": "Weekly grocery drop for Acme HQ",
        "is_active": True,
        "cadence": "WEEKLY",
        "interval_days": 1,
        "day_of_week_mask": 0b0010101,  # Mon, Wed, Fri
        "day_of_month": None,
        "start_date": date.today().isoformat(),
        "end_date": None,
        "max_occurrences": None,
        "delivery_window_start": "09:00:00",
        "delivery_window_end": "11:00:00",
        "pickup_window_start": None,
        "pickup_window_end": None,
        "lead_time_days": 14,
        "order_type": "SCHEDULED",
        "priority": "NORMAL",
        "pickup_address": "Warehouse A, Istanbul",
        "pickup_lat": 41.0431,
        "pickup_lon": 28.9026,
        "delivery_address": "Acme HQ, Beşiktaş",
        "delivery_lat": 41.0426,
        "delivery_lon": 29.0072,
        "weight_kg": 4.5,
        "is_fragile": False,
        "is_temp_sensitive": False,
        "customer_email": "ops@acme.example",
        "external_ref_prefix": "ACME-WEEKLY",
    }
    base.update(overrides)
    return base


def test_recurring_template_crud_and_run(live_test_client):
    owner = signup_owner(live_test_client, "recurring-crud")
    headers = auth_headers(owner["access_token"])

    create_resp = live_test_client.post(
        "/api/v1/recurring-orders",
        headers=headers,
        json=_template_payload("acme-weekly"),
    )
    assert create_resp.status_code == 201, create_resp.text
    template = create_resp.json()
    assert template["name"] == "acme-weekly"
    assert template["occurrences_generated"] == 0
    template_id = template["id"]

    # Duplicate name on same tenant -> 409.
    dup_resp = live_test_client.post(
        "/api/v1/recurring-orders",
        headers=headers,
        json=_template_payload("acme-weekly"),
    )
    assert dup_resp.status_code == 409

    list_resp = live_test_client.get("/api/v1/recurring-orders", headers=headers)
    assert list_resp.status_code == 200
    assert any(t["id"] == template_id for t in list_resp.json())

    preview_resp = live_test_client.get(
        f"/api/v1/recurring-orders/{template_id}/preview?horizon_days=14",
        headers=headers,
    )
    assert preview_resp.status_code == 200
    preview_dates = preview_resp.json()["occurrence_dates"]
    assert preview_dates, "weekly Mon/Wed/Fri template must hit at least one date in 14 days"

    # Run the template now — orders should appear.
    run_resp = live_test_client.post(
        f"/api/v1/recurring-orders/{template_id}/run",
        headers=headers,
    )
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert run_body["templates_evaluated"] >= 1
    assert run_body["occurrences_created"] >= 1

    # Calling run a second time must be a no-op (idempotency fence).
    run_again = live_test_client.post(
        f"/api/v1/recurring-orders/{template_id}/run",
        headers=headers,
    )
    assert run_again.status_code == 200
    assert run_again.json()["occurrences_created"] == 0

    # Generated orders show up in /api/v1/orders with the template back-pointer.
    orders_resp = live_test_client.get(
        "/api/v1/orders?limit=100", headers=headers
    )
    assert orders_resp.status_code == 200
    orders = orders_resp.json()
    template_orders = [
        o
        for o in orders
        if o.get("custom_fields", {}).get("recurring_template_id") == template_id
    ]
    assert template_orders, "no orders linked back to template"
    for order in template_orders:
        assert order["status"] == "PENDING"
        assert order["external_ref"].startswith("ACME-WEEKLY-")
        assert order["customer_email"] == "ops@acme.example"

    # Pause + delete cleans up DB rows.
    pause_resp = live_test_client.put(
        f"/api/v1/recurring-orders/{template_id}",
        headers=headers,
        json={"is_active": False},
    )
    assert pause_resp.status_code == 200
    paused = pause_resp.json()
    assert paused["is_active"] is False

    paused_run = live_test_client.post(
        f"/api/v1/recurring-orders/{template_id}/run",
        headers=headers,
    )
    assert paused_run.status_code == 409  # Cannot run paused template

    delete_resp = live_test_client.delete(
        f"/api/v1/recurring-orders/{template_id}",
        headers=headers,
    )
    assert delete_resp.status_code == 204


def test_recurring_template_max_occurrences_caps_generation(live_test_client):
    owner = signup_owner(live_test_client, "recurring-cap")
    headers = auth_headers(owner["access_token"])

    payload = _template_payload(
        "acme-capped",
        cadence="DAILY",
        day_of_week_mask=0,
        max_occurrences=3,
        lead_time_days=10,
    )
    create_resp = live_test_client.post(
        "/api/v1/recurring-orders", headers=headers, json=payload
    )
    assert create_resp.status_code == 201
    template_id = create_resp.json()["id"]

    run_resp = live_test_client.post(
        f"/api/v1/recurring-orders/{template_id}/run",
        headers=headers,
    )
    assert run_resp.status_code == 200
    assert run_resp.json()["occurrences_created"] == 3

    # Subsequent runs must not exceed the cap.
    run_again = live_test_client.post(
        f"/api/v1/recurring-orders/{template_id}/run",
        headers=headers,
    )
    assert run_again.json()["occurrences_created"] == 0


def test_recurring_template_inactive_skipped_by_global_run(live_test_client):
    owner = signup_owner(live_test_client, "recurring-inactive")
    headers = auth_headers(owner["access_token"])

    payload = _template_payload("acme-inactive", is_active=False)
    create_resp = live_test_client.post(
        "/api/v1/recurring-orders", headers=headers, json=payload
    )
    assert create_resp.status_code == 201

    run_resp = live_test_client.post("/api/v1/recurring-orders/run", headers=headers)
    assert run_resp.status_code == 200
    body = run_resp.json()
    # The materialiser must not pick up paused templates.
    assert body["templates_evaluated"] == 0
    assert body["occurrences_created"] == 0


def test_recurring_template_celery_task_is_idempotent(monkeypatch, live_test_client):
    """The celery beat task is the same path as the API run, so this
    primarily guards against accidental coupling to a particular session
    factory or transactional model."""
    owner = signup_owner(live_test_client, "recurring-celery")
    headers = auth_headers(owner["access_token"])
    me = _get_me(live_test_client, access_token=owner["access_token"])

    payload = _template_payload(
        "acme-celery",
        cadence="DAILY",
        day_of_week_mask=0,
        max_occurrences=2,
        lead_time_days=5,
    )
    create_resp = live_test_client.post(
        "/api/v1/recurring-orders", headers=headers, json=payload
    )
    assert create_resp.status_code == 201

    task_id = f"materialise-{uuid.uuid4().hex[:8]}"
    recurring_order_tasks.materialise_recurring_orders.push_request(id=task_id, retries=0)
    try:
        first = recurring_order_tasks.materialise_recurring_orders.run()
        second = recurring_order_tasks.materialise_recurring_orders.run()
    finally:
        recurring_order_tasks.materialise_recurring_orders.pop_request()

    assert first["occurrences_created"] >= 1
    assert second["occurrences_created"] == 0

    # Verify orders were created with the right tenant binding.
    async def _check() -> int:
        async with get_async_session_factory()() as db:
            rows = (
                await db.execute(
                    select(Order).where(Order.tenant_id == uuid.UUID(me["tenant_id"]))
                )
            ).scalars().all()
            return len(rows)

    n_orders = run_async(_check())
    assert n_orders >= 1


def test_validation_rejects_inverted_delivery_window(live_test_client):
    owner = signup_owner(live_test_client, "recurring-validate")
    headers = auth_headers(owner["access_token"])
    payload = _template_payload(
        "acme-bad-window",
        delivery_window_start="14:00:00",
        delivery_window_end="13:00:00",
    )
    resp = live_test_client.post("/api/v1/recurring-orders", headers=headers, json=payload)
    assert resp.status_code == 422


def test_validation_rejects_pdp_template_without_pickup_window(live_test_client):
    owner = signup_owner(live_test_client, "recurring-pdp")
    headers = auth_headers(owner["access_token"])
    payload = _template_payload(
        "acme-pdp",
        order_type="PICKUP_AND_DELIVERY",
        # pickup_window_* deliberately omitted
    )
    resp = live_test_client.post("/api/v1/recurring-orders", headers=headers, json=payload)
    assert resp.status_code == 422
