import uuid
from datetime import datetime
from types import SimpleNamespace

from app.models.enums import OrderPriority, OrderStatus, OrderType
from app.utils.compat import UTC
from tests.integration.test_utils import build_test_client, find_route_dependency


class _OneResult:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarRows(self._rows)


class _FakeDBSession:
    def __init__(self, execute_results):
        self._execute_results = list(execute_results)

    async def execute(self, _query):
        return self._execute_results.pop(0)


def _build_overrides() -> dict:
    async def _override_role():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="ADMIN", is_active=True)

    role_dep = find_route_dependency("/api/v1/analytics/summary", "current_user")
    return {role_dep: _override_role}


def test_analytics_summary_endpoint_returns_expected_rates():
    # First execute() returns aggregated totals; second returns the per-status
    # breakdown (status -> count). Statuses missing from the rows fall back to
    # zero in the service so we exercise both populated and zero-filled cases.
    breakdown_rows = [
        (OrderStatus.DELIVERED, 7),
        (OrderStatus.FAILED, 2),
        (OrderStatus.PENDING, 1),
    ]
    fake_db = _FakeDBSession([
        _OneResult((10, 7, 2, 1, 6)),
        _RowsResult(breakdown_rows),
    ])
    with build_test_client(db_session=fake_db, dependency_overrides=_build_overrides()) as client:
        response = client.get("/api/v1/analytics/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_orders"] == 10
    assert body["delivered_orders"] == 7
    assert body["failed_orders"] == 2
    assert body["pending_orders"] == 1
    assert body["completion_rate"] == 0.7
    assert body["failed_rate"] == 0.2
    assert body["on_time_rate"] == 0.8571
    breakdown = body["status_breakdown"]
    assert breakdown["DELIVERED"] == 7
    assert breakdown["FAILED"] == 2
    assert breakdown["PENDING"] == 1
    # Statuses not present in the GROUP BY rows are zero-filled.
    assert breakdown["DRAFT"] == 0
    assert breakdown["ASSIGNED"] == 0
    assert breakdown["IN_TRANSIT"] == 0


def test_analytics_courier_table_endpoint_returns_rows():
    courier_id = uuid.uuid4()
    fake_db = _FakeDBSession([_RowsResult([(courier_id, 12, 9, 3)])])
    with build_test_client(db_session=fake_db, dependency_overrides=_build_overrides()) as client:
        response = client.get("/api/v1/analytics/couriers")

    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["courier_id"] == str(courier_id)
    assert body["rows"][0]["assigned_orders"] == 12
    assert body["rows"][0]["delivered_orders"] == 9
    assert body["rows"][0]["failed_orders"] == 3


def test_exception_center_endpoint_returns_grouped_exceptions(monkeypatch):
    from app.api import analytics as analytics_api

    async def _mock_exception_center(_db, _principal):
        return {
            "counts": {
                "pending_approval_runs": 1,
                "critical_alerts": 2,
                "failed_orders": 3,
                "unassigned_orders": 4,
                "total_open_items": 10,
            },
            "pending_run": {
                "id": uuid.uuid4(),
                "task_id": "task-1",
                "run_type": "BATCH",
                "status": "PENDING_APPROVAL",
                "routes_created": 3,
                "unassigned_count": 1,
                "solver_status": "ROUTING_SUCCESS",
                "metrics": None,
                "error_message": None,
                "started_at": datetime.now(UTC).replace(tzinfo=None),
                "finished_at": None,
            },
            "critical_alerts": [
                {
                    "id": uuid.uuid4(),
                    "route_id": uuid.uuid4(),
                    "stop_id": None,
                    "order_id": None,
                    "courier_id": uuid.uuid4(),
                    "severity": "CRITICAL",
                    "status": "ACTIVE",
                    "alert_type": "LATE_DELIVERY",
                    "message": "Courier is behind",
                    "estimated_delay_seconds": 900,
                    "created_at": "2026-03-01T09:00:00",
                    "updated_at": "2026-03-01T09:05:00",
                    "acknowledged_at": None,
                }
            ],
            "failed_orders": [_build_order_response(uuid.uuid4(), status=OrderStatus.FAILED)],
            "unassigned_orders": [_build_order_response(uuid.uuid4(), status=OrderStatus.PENDING)],
        }

    monkeypatch.setattr(
        analytics_api.analytics_service,
        "get_exception_center_summary",
        _mock_exception_center,
    )

    with build_test_client(dependency_overrides=_build_overrides()) as client:
        response = client.get("/api/v1/analytics/exception-center")

    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["total_open_items"] == 10
    assert body["pending_run"]["status"] == "PENDING_APPROVAL"
    assert body["critical_alerts"][0]["severity"] == "CRITICAL"
    assert body["failed_orders"][0]["status"] == "FAILED"
    assert body["unassigned_orders"][0]["status"] == "PENDING"


def test_analytics_export_endpoint_returns_csv_content():
    order_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    now = datetime.now(UTC).replace(tzinfo=None)
    fake_order = SimpleNamespace(
        id=order_id,
        type=OrderType.ON_DEMAND,
        status=OrderStatus.DELIVERED,
        priority=OrderPriority.HIGH,
        assigned_courier_id=courier_id,
        created_at=now,
        updated_at=now,
        latest_delivery=now,
        attempt_count=1,
    )
    fake_db = _FakeDBSession([_ScalarsResult([fake_order])])
    with build_test_client(db_session=fake_db, dependency_overrides=_build_overrides()) as client:
        response = client.get("/api/v1/analytics/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=orders_export.csv" in response.headers["content-disposition"]

    csv_text = response.text
    assert "order_id,type,status,priority,assigned_courier_id,created_at,updated_at,latest_delivery,attempt_count" in csv_text
    assert str(order_id) in csv_text
    assert "ON_DEMAND" in csv_text
    assert "DELIVERED" in csv_text


def test_optimization_savings_endpoint_aggregates_baseline_vs_selected(monkeypatch):
    """The endpoint should compute distance/duration/on-time/cost savings by
    diffing the human_dispatcher_baseline scenario against the selected one,
    aggregated across all contributing comparisons in the range."""
    from app.api import analytics as analytics_api

    async def _mock_savings(_db, _principal, *, date_from=None, date_to=None):  # noqa: ARG001
        return {
            "comparisons_count": 3,
            "totals": {
                "baseline_distance_km": 480.0,
                "optimized_distance_km": 360.0,
                "distance_saved_km": 120.0,
                "distance_saved_pct": 0.25,
                "baseline_duration_min": 1200.0,
                "optimized_duration_min": 960.0,
                "duration_saved_min": 240.0,
                "duration_saved_pct": 0.20,
                "baseline_on_time_rate": 0.70,
                "optimized_on_time_rate": 0.92,
                "on_time_rate_delta": 0.22,
                "late_minutes_saved": 145,
                "energy_cost_saved_try": 312.75,
                "fuel_saved_liters": 18.3,
                "co2_saved_kg": 41.8,
                "routes_saved": 2,
            },
            "per_day": [
                {
                    "date": "2026-05-09",
                    "selected_scenario_key": "rich_alns_service_first",
                    "baseline_distance_km": 160.0,
                    "optimized_distance_km": 120.0,
                    "baseline_duration_min": 400.0,
                    "optimized_duration_min": 320.0,
                    "baseline_on_time_rate": 0.70,
                    "optimized_on_time_rate": 0.92,
                    "baseline_energy_cost_try": 200.0,
                    "optimized_energy_cost_try": 150.0,
                    "baseline_co2_kg": 30.0,
                    "optimized_co2_kg": 22.0,
                },
            ],
            "solver_mix": [
                {"scenario_key": "rich_alns_service_first", "count": 2},
                {"scenario_key": "rich_alns_fuel_aware", "count": 1},
            ],
        }

    monkeypatch.setattr(
        analytics_api.analytics_service,
        "get_optimization_savings",
        _mock_savings,
    )

    with build_test_client(dependency_overrides=_build_overrides()) as client:
        response = client.get("/api/v1/analytics/optimization-savings")

    assert response.status_code == 200
    body = response.json()
    assert body["comparisons_count"] == 3
    assert body["totals"]["distance_saved_km"] == 120.0
    assert body["totals"]["distance_saved_pct"] == 0.25
    assert body["totals"]["on_time_rate_delta"] == 0.22
    assert body["totals"]["co2_saved_kg"] == 41.8
    assert body["per_day"][0]["selected_scenario_key"] == "rich_alns_service_first"
    assert body["solver_mix"][0]["count"] == 2


def test_runtime_performance_endpoint_returns_metrics_snapshot(monkeypatch):
    from app.api import analytics as analytics_api

    role_dep = find_route_dependency("/api/v1/analytics/runtime-performance", "_user")

    async def _override_role():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="ADMIN", is_active=True)

    monkeypatch.setattr(
        analytics_api.analytics_service,
        "get_runtime_performance_snapshot",
        lambda: {
            "dispatch_batch_latency_ms": {"count": 3, "p50_ms": 120.0, "p95_ms": 160.0, "avg_ms": 130.0, "median_ms": 125.0},
            "dispatch_on_demand_latency_ms": {"count": 5, "p50_ms": 90.0, "p95_ms": 130.0, "avg_ms": 100.0, "median_ms": 98.0},
            "eta_compute_latency_ms": {"count": 8, "p50_ms": 40.0, "p95_ms": 75.0, "avg_ms": 45.0, "median_ms": 44.0},
            "eta_error_delta_ms": {"count": 8, "p50_ms": 0.0, "p95_ms": 5000.0, "avg_ms": 1200.0, "median_ms": 600.0},
            "osrm": {"osrm_table_success_count": 12, "osrm_table_error_count": 0},
        },
    )

    with build_test_client(dependency_overrides={role_dep: _override_role}) as client:
        response = client.get("/api/v1/analytics/runtime-performance")

    assert response.status_code == 200
    body = response.json()
    assert body["dispatch_batch_latency_ms"]["count"] == 3
    assert body["dispatch_on_demand_latency_ms"]["p95_ms"] == 130.0
    assert body["osrm"]["osrm_table_success_count"] == 12


def test_runtime_performance_prometheus_endpoint_returns_scrape_text(monkeypatch):
    from app.api import analytics as analytics_api

    role_dep = find_route_dependency(
        "/api/v1/analytics/runtime-performance/prometheus",
        "_user",
    )

    async def _override_role():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="ADMIN", is_active=True)

    monkeypatch.setattr(
        analytics_api.analytics_service,
        "get_runtime_performance_metrics_text",
        lambda: (
            "# Generated from in-memory backend runtime metrics.\n"
            "lastmile_dispatch_batch_latency_ms_count 2\n"
            "lastmile_webhook_delivery_success_total 4\n"
        ),
    )

    with build_test_client(dependency_overrides={role_dep: _override_role}) as client:
        response = client.get("/api/v1/analytics/runtime-performance/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "lastmile_dispatch_batch_latency_ms_count 2" in response.text
    assert "lastmile_webhook_delivery_success_total 4" in response.text


def _build_order_response(order_id: uuid.UUID, *, status: OrderStatus) -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    return {
        "id": order_id,
        "type": OrderType.ON_DEMAND,
        "status": status,
        "priority": OrderPriority.NORMAL,
        "pickup_address": "Warehouse A",
        "pickup_lat": 41.0,
        "pickup_lon": 29.0,
        "delivery_address": "Customer Address",
        "delivery_lat": 41.01,
        "delivery_lon": 29.01,
        "weight_kg": 1.5,
        "is_fragile": False,
        "is_temp_sensitive": False,
        "earliest_pickup": None,
        "latest_pickup": None,
        "earliest_delivery": now,
        "latest_delivery": now,
        "depot_id": None,
        "assigned_courier_id": None,
        "preferred_courier_id": None,
        "route_id": None,
        "customer_email": "ops@example.com",
        "customer_phone": None,
        "tracking_token": "tracking-token",
        "delivery_otp_code": None,
        "external_ref": None,
        "custom_fields": None,
        "attempt_count": 0,
        "max_attempts": 3,
        "failed_at": None,
        "customer_response_status": None,
        "customer_response_due_at": None,
        "escalated_at": None,
        "notes": None,
        "created_at": now,
        "updated_at": now,
    }
