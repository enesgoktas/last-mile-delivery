"""
API contract tests: snapshot-style assertions against the OpenAPI schema.

These tests do NOT connect to a database. They simply generate the schema from
the registered FastAPI routes and assert that key response structures match what
frontend/mobile clients expect.

If you rename a field in a Pydantic schema the tests here will fail, reminding
you to regenerate the checked-in OpenAPI artifacts and update any app-specific
wrappers that intentionally extend the generated shared contract layer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENAPI_SNAPSHOT_PATH = REPO_ROOT / "shared" / "openapi.generated.json"


@pytest.fixture(scope="module")
def openapi_schema():
    from app.config import settings
    settings.SKIP_DB_STARTUP_CHECKS = True

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, "OpenAPI endpoint must be reachable"
    return resp.json()


# ── Schema helpers ────────────────────────────────────────────────────────────

def get_schema(openapi: dict, name: str) -> dict:
    schemas = openapi.get("components", {}).get("schemas", {})
    if name not in schemas and f"{name}-Output" in schemas:
        return schemas[f"{name}-Output"]
    if name not in schemas and f"{name}-Input" in schemas:
        return schemas[f"{name}-Input"]
    assert name in schemas, f"Schema '{name}' not found in OpenAPI components. Available: {sorted(schemas)}"
    return schemas[name]


def required_fields(schema: dict) -> set[str]:
    return set(schema.get("required", []))


def property_names(schema: dict) -> set[str]:
    return set(schema.get("properties", {}).keys())


# ── Order schema ──────────────────────────────────────────────────────────────

class TestOrderSchema:
    def test_order_response_has_expected_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "OrderResponse")
        props = property_names(schema)
        expected = {
            "id", "type", "status", "priority",
            "pickup_address", "pickup_lat", "pickup_lon",
            "delivery_address", "delivery_lat", "delivery_lon",
            "weight_kg", "scheduled_for_date", "preferred_courier_id", "created_at",
        }
        missing = expected - props
        assert not missing, f"OrderResponse is missing fields: {missing}"

    def test_order_response_status_is_enum(self, openapi_schema):
        schema = get_schema(openapi_schema, "OrderResponse")
        status_field = schema["properties"]["status"]
        # May be a $ref or inline enum
        if "$ref" in status_field:
            ref_name = status_field["$ref"].split("/")[-1]
            status_schema = get_schema(openapi_schema, ref_name)
            assert "enum" in status_schema
        else:
            # Could be anyOf / allOf pointing to enum
            assert "$ref" in str(status_field), f"Expected status to reference an enum; got: {status_field}"

    def test_create_order_request_has_required_address_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "OrderCreate")
        req = required_fields(schema)
        for field in ("pickup_address", "delivery_address", "weight_kg"):
            assert field in req, f"'{field}' must be required in OrderCreate"


# ── Route schema ──────────────────────────────────────────────────────────────

class TestRouteSchema:
    def test_route_response_has_expected_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "RouteResponse")
        props = property_names(schema)
        expected = {"id", "courier_id", "status", "stops", "created_at"}
        missing = expected - props
        assert not missing, f"RouteResponse is missing fields: {missing}"

    def test_route_stop_response_has_expected_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "RouteStopResponse")
        props = property_names(schema)
        expected = {"id", "order_id", "sequence_number", "stop_type", "status", "address", "lat", "lon", "customer_phone"}
        missing = expected - props
        assert not missing, f"RouteStopResponse is missing fields: {missing}"

    def test_run_preview_response_exposes_explainability(self, openapi_schema):
        schema = get_schema(openapi_schema, "RunPreviewResponse")
        props = property_names(schema)
        expected = {"optimization_run_id", "routes", "summary", "explainability"}
        missing = expected - props
        assert not missing, f"RunPreviewResponse is missing fields: {missing}"

    def test_preview_route_response_exposes_route_explainability(self, openapi_schema):
        schema = get_schema(openapi_schema, "PreviewRouteResponse")
        props = property_names(schema)
        expected = {
            "route_id",
            "courier_id",
            "violations",
            "stops",
            "explainability",
            "cost_breakdown",
            "cost_delta_vs_current",
        }
        missing = expected - props
        assert not missing, f"PreviewRouteResponse is missing fields: {missing}"

    def test_preview_summary_response_exposes_economic_costs(self, openapi_schema):
        schema = get_schema(openapi_schema, "PreviewSummary")
        props = property_names(schema)
        expected = {"cost_breakdown", "cost_delta_vs_current"}
        missing = expected - props
        assert not missing, f"PreviewSummary is missing fields: {missing}"


# ── Operations policy schema ──────────────────────────────────────────────────

class TestOperationsPolicySchema:
    def test_operations_policy_has_top_level_sections(self, openapi_schema):
        # Pydantic v2 emits a single combined `OperationsPolicy` schema when
        # the input/output shapes match (which is the case here). Older
        # Pydantic split it into `-Input` / `-Output`; either is fine —
        # accept whichever the active Pydantic version produces.
        schemas = openapi_schema["components"]["schemas"]
        candidate_name = next(
            (name for name in ("OperationsPolicy-Output", "OperationsPolicy") if name in schemas),
            None,
        )
        assert candidate_name is not None, (
            "Expected an `OperationsPolicy` schema in the OpenAPI components"
        )
        schema = get_schema(openapi_schema, candidate_name)
        props = property_names(schema)
        expected = {
            "dispatch",
            "optimization",
            "delivery_workflow",
            "proof_of_delivery",
            "failure_handling",
            "notifications",
            "order_form",
            "team_permissions",
        }
        missing = expected - props
        assert not missing, f"OperationsPolicy is missing sections: {missing}"

    def test_dispatch_settings_has_mode_field(self, openapi_schema):
        schema = get_schema(openapi_schema, "DispatchSettings")
        assert "dispatch_mode" in property_names(schema)
        assert "assignment_strategy" in property_names(schema)

    def test_optimization_settings_exposes_fuel_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "OptimizationSettings")
        props = property_names(schema)
        assert "fuel_priority_multiplier" in props, (
            "OptimizationSettings must expose fuel_priority_multiplier so the "
            "frontend fuel-routing control can read and persist it"
        )
        assert "fuel_prices" in props, (
            "OptimizationSettings must expose fuel_prices for per-fuel-type cost derivation"
        )

    def test_proof_of_delivery_requirements_has_photo_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "ProofOfDeliveryRequirements")
        props = property_names(schema)
        assert "require_photo" in props
        assert "photo_count" in props
        assert "require_signature" in props
        assert "require_otp" in props


# ── Courier schema ────────────────────────────────────────────────────────────

class TestCourierSchema:
    def test_courier_response_has_location_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "CourierResponse")
        props = property_names(schema)
        expected = {"id", "user_id", "vehicle_type", "availability_status", "depot_lat", "depot_lon"}
        missing = expected - props
        assert not missing, f"CourierResponse is missing fields: {missing}"


# ── SLA Alert schema ──────────────────────────────────────────────────────────

class TestSLAAlertSchema:
    def test_sla_alert_response_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "SLAAlertResponse")
        props = property_names(schema)
        expected = {"id", "courier_id", "severity", "message", "status", "created_at"}
        missing = expected - props
        assert not missing, f"SLAAlertResponse is missing fields: {missing}"

    def test_sla_alert_severity_is_string_field(self, openapi_schema):
        schema = get_schema(openapi_schema, "SLAAlertResponse")
        severity = schema["properties"]["severity"]
        assert severity.get("type") == "string", f"severity should serialize as string; got: {severity}"


# ── Audit log schema ──────────────────────────────────────────────────────────

class TestAuditLogSchema:
    def test_audit_log_response_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "AuditLogResponse")
        props = property_names(schema)
        expected = {"id", "tenant_id", "actor_user_id", "action", "resource_type", "resource_id", "created_at"}
        missing = expected - props
        assert not missing, f"AuditLogResponse is missing fields: {missing}"


# ── Auth schema ───────────────────────────────────────────────────────────────

class TestAuthSchema:
    def test_token_response_has_access_and_refresh(self, openapi_schema):
        schema = get_schema(openapi_schema, "TokenResponse")
        props = property_names(schema)
        assert "access_token" in props, "TokenResponse must have access_token"
        assert "refresh_token" in props, "TokenResponse must have refresh_token"
        assert "token_type" in props, "TokenResponse must have token_type"

    def test_login_request_has_expected_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "LoginRequest")
        props = property_names(schema)
        assert "email" in props
        assert "password" in props


# ── Analytics schema ──────────────────────────────────────────────────────────

class TestAnalyticsSchema:
    def test_analytics_summary_has_kpi_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "AnalyticsSummaryResponse")
        props = property_names(schema)
        expected = {"total_orders", "delivered_orders", "failed_orders", "completion_rate", "on_time_rate"}
        missing = expected - props
        assert not missing, f"AnalyticsSummary is missing KPI fields: {missing}"

    def test_exception_center_summary_has_grouped_exception_fields(self, openapi_schema):
        schema = get_schema(openapi_schema, "ExceptionCenterSummaryResponse")
        props = property_names(schema)
        expected = {"counts", "pending_run", "critical_alerts", "failed_orders", "unassigned_orders"}
        missing = expected - props
        assert not missing, f"ExceptionCenterSummary is missing fields: {missing}"


# ── Route coverage assertion ──────────────────────────────────────────────────

class TestRoutePresence:
    """Verify that key API routes are present in the schema."""

    def _path_exists(self, openapi: dict, path: str, method: str) -> bool:
        return method.lower() in openapi.get("paths", {}).get(path, {})

    def test_order_routes_present(self, openapi_schema):
        assert self._path_exists(openapi_schema, "/api/v1/orders", "get"), "GET /orders missing"
        assert self._path_exists(openapi_schema, "/api/v1/orders", "post"), "POST /orders missing"
        assert self._path_exists(openapi_schema, "/api/v1/orders/{order_id}/preferred-courier", "put")

    def test_route_routes_present(self, openapi_schema):
        assert self._path_exists(openapi_schema, "/api/v1/routes", "get"), "GET /routes missing"

    def test_auth_routes_present(self, openapi_schema):
        assert self._path_exists(openapi_schema, "/api/v1/auth/login", "post"), "POST /auth/login missing"
        assert self._path_exists(openapi_schema, "/api/v1/auth/refresh", "post"), "POST /auth/refresh missing"

    def test_operations_policy_routes_present(self, openapi_schema):
        assert self._path_exists(openapi_schema, "/api/v1/settings/operations-policy", "get")
        assert self._path_exists(openapi_schema, "/api/v1/settings/operations-policy", "put")
        assert self._path_exists(openapi_schema, "/api/v1/settings/operations-policy", "patch")

    def test_audit_log_route_present(self, openapi_schema):
        assert self._path_exists(openapi_schema, "/api/v1/audit-logs", "get"), "GET /audit-logs missing"

    def test_analytics_routes_present(self, openapi_schema):
        assert self._path_exists(openapi_schema, "/api/v1/analytics/exception-center", "get")


class TestGeneratedOpenAPIArtifacts:
    def test_generated_openapi_snapshot_is_current(self, openapi_schema):
        # The snapshot lives in `<repo>/shared/openapi.generated.json`.
        # When the test runs inside the api docker container only the
        # `backend/` tree is mounted, so the file is genuinely absent —
        # this isn't a stale-snapshot signal, it's a test-isolation one.
        # Skip cleanly so CI on the host still enforces the contract while
        # in-container `pytest` runs keep working.
        if not OPENAPI_SNAPSHOT_PATH.exists():
            pytest.skip(
                f"OpenAPI snapshot not reachable at {OPENAPI_SNAPSHOT_PATH} — "
                "run from the repo root for the contract check"
            )
        snapshot = json.loads(OPENAPI_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert snapshot == openapi_schema, (
            "shared/openapi.generated.json is stale. "
            "Run `shared/scripts/generate_api_contract.sh` from the repo root."
        )
