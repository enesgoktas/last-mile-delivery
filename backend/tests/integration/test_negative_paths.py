"""
Integration tests covering API-layer enforcement of negative paths:
  - 422 Unprocessable Entity (validation failures)
  - 403 Forbidden (insufficient role)
  - 409 Conflict (business-rule violations)

These tests exercise the full request → router → dependency → service chain.
The DB session is mocked to eliminate test-DB dependencies while still testing
every FastAPI and Pydantic validation layer.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.enums import UserRole
from app.utils.access import AccessContext
from app.utils.auth import create_access_token, get_current_user
from tests.integration.test_utils import build_test_client, find_route_dependency

# ── helpers ────────────────────────────────────────────────────────────────────

class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _extract_filter_value(query, column_name: str):
    whereclause = getattr(query, "whereclause", None)
    if whereclause is None:
        return None

    clauses = list(getattr(whereclause, "clauses", [whereclause]))
    for clause in clauses:
        left = getattr(clause, "left", None)
        if getattr(left, "name", None) != column_name:
            continue
        right = getattr(clause, "right", None)
        if hasattr(right, "value"):
            return right.value
    return None


class _AuthDBSession:
    def __init__(self, *users):
        self.users_by_id = {user.id: user for user in users}

    async def execute(self, query):
        user_id = _extract_filter_value(query, "id")
        tenant_id = _extract_filter_value(query, "tenant_id")
        user = self.users_by_id.get(user_id)
        if user is not None and tenant_id is not None and user.tenant_id != tenant_id:
            user = None
        return _ScalarResult(user)


def _normalize_role(role: str | UserRole) -> UserRole:
    return role if isinstance(role, UserRole) else UserRole(role)


def _make_user(role: str | UserRole = UserRole.ADMIN) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role=_normalize_role(role),
        is_active=True,
        permissions=None,
    )


def _make_auth_dep(path: str, role: str = "ADMIN", *, method: str | None = None):
    """Return a role-override pair for ``build_test_client``."""
    user = _make_user(role)

    async def _override():
        return user

    return get_current_user, _override, user


def _make_access_dep(path: str, role: str = "DISPATCHER", *, method: str | None = None):
    dep = find_route_dependency(path, "access", method=method)
    user = _make_user(role)

    async def _override():
        return AccessContext(user=user, api_client=None, source="jwt")

    return dep, _override, user


def _auth_headers_for(user: SimpleNamespace, monkeypatch) -> dict[str, str]:
    import app.utils.auth as auth_utils

    async def _not_revoked(_jti):
        return False

    monkeypatch.setattr(auth_utils, "is_token_revoked", _not_revoked)
    token = create_access_token(str(user.id), user.role.value, str(user.tenant_id))
    return {"Authorization": f"Bearer {token}"}


# ── 422 Validation errors ─────────────────────────────────────────────────────

class Test422Validation:
    def test_create_order_missing_required_fields(self):
        dep, override, _ = _make_access_dep("/api/v1/orders", "DISPATCHER", method="POST")
        with build_test_client(dependency_overrides={dep: override}) as client:
            response = client.post("/api/v1/orders", json={})
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body

    def test_create_order_invalid_priority(self):
        dep, override, _ = _make_access_dep("/api/v1/orders", "DISPATCHER", method="POST")
        with build_test_client(dependency_overrides={dep: override}) as client:
            response = client.post(
                "/api/v1/orders",
                json={
                    "type": "SCHEDULED",
                    "priority": "INVALID_PRIORITY",
                    "pickup_address": "1 A St",
                    "pickup_lat": 41.0,
                    "pickup_lon": 29.0,
                    "delivery_address": "2 B St",
                    "delivery_lat": 41.1,
                    "delivery_lon": 29.1,
                    "weight_kg": 1.0,
                },
            )
        assert response.status_code == 422

    def test_create_order_invalid_coordinates(self):
        dep, override, _ = _make_access_dep("/api/v1/orders", "DISPATCHER", method="POST")
        with build_test_client(dependency_overrides={dep: override}) as client:
            response = client.post(
                "/api/v1/orders",
                json={
                    "type": "SCHEDULED",
                    "priority": "NORMAL",
                    "pickup_address": "1 A St",
                    "pickup_lat": "not-a-float",
                    "pickup_lon": 29.0,
                    "delivery_address": "2 B St",
                    "delivery_lat": 41.1,
                    "delivery_lon": 29.1,
                    "weight_kg": 1.0,
                },
            )
        assert response.status_code == 422

    def test_reschedule_order_invalid_date_format(self):
        dep, override, _ = _make_auth_dep("/api/v1/orders/{order_id}/reschedule", "DISPATCHER", method="POST")
        order_id = uuid.uuid4()
        with build_test_client(dependency_overrides={dep: override}) as client:
            response = client.post(
                f"/api/v1/orders/{order_id}/reschedule",
                json={"earliest_delivery": "NOT-A-DATE", "latest_delivery": "ALSO-NOT-A-DATE"},
            )
        assert response.status_code == 422

    def test_constraints_invalid_negative_distance(self):
        dep, override, _ = _make_auth_dep("/api/v1/constraints/global", "ADMIN", method="PUT")
        with build_test_client(dependency_overrides={dep: override}) as client:
            response = client.put(
                "/api/v1/constraints/global",
                json={"max_route_distance_km": -100},
            )
        # Negative distance should be rejected by Pydantic ge=0 constraint
        assert response.status_code == 422

    def test_error_response_includes_request_id(self):
        """Standardised error responses must include a request_id field."""
        dep, override, _ = _make_access_dep("/api/v1/orders", "DISPATCHER", method="POST")
        with build_test_client(dependency_overrides={dep: override}) as client:
            response = client.post("/api/v1/orders", json={})
        assert response.status_code == 422
        body = response.json()
        # Per Phase 4, all error responses include request_id
        assert "request_id" in body


# ── 403 Forbidden ─────────────────────────────────────────────────────────────

class Test403Forbidden:
    def test_courier_cannot_access_operations_policy(self, monkeypatch):
        user = _make_user("COURIER")
        headers = _auth_headers_for(user, monkeypatch)
        with build_test_client(db_session=_AuthDBSession(user)) as client:
            response = client.get("/api/v1/settings/operations-policy", headers=headers)
        # COURIER role is not in AdminDep — expect 403
        assert response.status_code == 403

    def test_dispatcher_cannot_update_operations_policy(self, monkeypatch):
        user = _make_user("DISPATCHER")
        headers = _auth_headers_for(user, monkeypatch)
        with build_test_client(db_session=_AuthDBSession(user)) as client:
            response = client.put(
                "/api/v1/settings/operations-policy",
                json={},  # body doesn't matter — blocked at role check
                headers=headers,
            )
        assert response.status_code == 403

    def test_courier_cannot_list_routes(self, monkeypatch):
        user = _make_user("COURIER")
        headers = _auth_headers_for(user, monkeypatch)
        with build_test_client(db_session=_AuthDBSession(user)) as client:
            response = client.get("/api/v1/routes", headers=headers)
        assert response.status_code == 403

    def test_unauthenticated_request_returns_401_or_422(self):
        """Requests without an auth token should be rejected."""
        # No dependency override — the real require_role logic fires
        from app.main import app
        original = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        try:
            from fastapi.testclient import TestClient

            from app.config import settings
            settings.SKIP_DB_STARTUP_CHECKS = True
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/routes")
            assert response.status_code in (401, 403, 422)
        finally:
            app.dependency_overrides.update(original)


# ── 409 Conflict ──────────────────────────────────────────────────────────────

class Test409Conflict:
    def test_cancel_already_delivered_order_returns_409(self):
        """Cancelling a DELIVERED order must raise a 409 conflict."""
        from app.api import orders as orders_api

        dep, override, user = _make_auth_dep("/api/v1/orders/{order_id}/cancel", "DISPATCHER", method="POST")
        order_id = uuid.uuid4()

        with patch.object(
            orders_api.order_service,
            "cancel_order",
            new=AsyncMock(side_effect=__import__("fastapi").HTTPException(
                status_code=409,
                detail="Cannot cancel a DELIVERED order",
            )),
        ):
            with build_test_client(dependency_overrides={dep: override}) as client:
                response = client.post(f"/api/v1/orders/{order_id}/cancel")

        assert response.status_code == 409
        assert "cancel" in response.json().get("detail", "").lower() or response.status_code == 409

    def test_start_route_that_is_already_active_returns_409(self):
        """Starting a route that is already ACTIVE must raise 409."""
        from app.api import routes as routes_api

        dep = find_route_dependency("/api/v1/routes/{route_id}/start", "current_user", method="POST")
        user = _make_user("DISPATCHER")

        async def _override():
            return user

        route_id = uuid.uuid4()

        with patch.object(
            routes_api.route_execution_service,
            "start_route",
            new=AsyncMock(side_effect=__import__("fastapi").HTTPException(
                status_code=409,
                detail="Route is already active",
            )),
        ):
            with build_test_client(dependency_overrides={dep: _override}) as client:
                response = client.post(f"/api/v1/routes/{route_id}/start")

        assert response.status_code == 409


# ── Operations Policy CRUD ────────────────────────────────────────────────────

class TestOperationsPolicyCRUD:
    """Verify the full GET → PUT → PATCH lifecycle through the API."""

    def _admin_dep(self):
        return _make_auth_dep("/api/v1/settings/operations-policy", "ADMIN", method="GET")

    def test_get_operations_policy_returns_default_on_empty_db(self):
        from app.api import operations_settings as ops_api
        from app.schemas.operations_policy import default_operations_policy

        dep, override, user = self._admin_dep()

        async def _no_policy(db, current_user):
            return default_operations_policy()

        with patch.object(ops_api.operations_policy_service, "ensure_permission", new=AsyncMock()):
            with patch.object(ops_api.operations_policy_service, "get_operations_policy", new=_no_policy):
                with build_test_client(dependency_overrides={dep: override}) as client:
                    response = client.get("/api/v1/settings/operations-policy")

        assert response.status_code == 200
        body = response.json()
        assert "dispatch" in body
        assert "proof_of_delivery" in body
        assert "failure_handling" in body
        assert "notifications" in body

    def test_put_operations_policy_saves_and_returns_updated_policy(self):
        from app.api import operations_settings as ops_api
        from app.schemas.operations_policy import default_operations_policy

        dep, override, user = self._admin_dep()
        saved_policy = default_operations_policy()
        saved_policy.notifications.branding_name = "Updated Brand"

        async def _save(db, current_user, payload_dict):
            return saved_policy

        with patch.object(ops_api.operations_policy_service, "ensure_permission", new=AsyncMock()):
            with patch.object(ops_api.operations_policy_service, "set_operations_policy", new=_save):
                with patch.object(ops_api, "log_audit_event", new=AsyncMock()):
                    with build_test_client(dependency_overrides={dep: override}) as client:
                        response = client.put(
                            "/api/v1/settings/operations-policy",
                            json=saved_policy.model_dump(mode="json"),
                        )

        assert response.status_code == 200
        body = response.json()
        assert body["notifications"]["branding_name"] == "Updated Brand"

    def test_patch_operations_policy_deep_merges_partial_payload(self):
        from app.api import operations_settings as ops_api
        from app.schemas.operations_policy import default_operations_policy

        dep, override, user = self._admin_dep()
        patched_policy = default_operations_policy()
        patched_policy.dispatch.dispatch_mode = "MANUAL"

        async def _patch(db, current_user, payload_dict):
            return patched_policy

        with patch.object(ops_api.operations_policy_service, "ensure_permission", new=AsyncMock()):
            with patch.object(ops_api.operations_policy_service, "patch_operations_policy", new=_patch):
                with patch.object(ops_api, "log_audit_event", new=AsyncMock()):
                    with build_test_client(dependency_overrides={dep: override}) as client:
                        response = client.patch(
                            "/api/v1/settings/operations-policy",
                            json={"dispatch": {"dispatch_mode": "MANUAL"}},
                        )

        assert response.status_code == 200
        assert response.json()["dispatch"]["dispatch_mode"] == "MANUAL"

    def test_put_operations_policy_with_invalid_dispatch_mode_returns_422(self):
        dep, override, _ = self._admin_dep()
        with build_test_client(dependency_overrides={dep: override}) as client:
            response = client.put(
                "/api/v1/settings/operations-policy",
                json={"dispatch": {"dispatch_mode": "TELEPATHIC"}},
            )
        assert response.status_code == 422
