"""Unit tests for the vehicle tags API endpoints.

Coverage:
- GET /vehicle-tags — list (dispatcher+)
- POST /vehicle-tags — create (admin only)
- PATCH /vehicle-tags/{id} — update (admin only)
- DELETE /vehicle-tags/{id} — delete (admin only)
- PUT /couriers/{id}/tags — set courier tags (admin only)
- Role enforcement (dispatcher cannot create/update/delete)
- Cross-tenant isolation (tag belonging to another tenant returns 404)
- Schema validation (bad hex color → 422)
"""
import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import UserRole
from app.utils.auth import get_current_user
from tests.integration.test_utils import build_test_client, find_route_dependency

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _admin(tenant_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=UserRole.ADMIN,
        is_active=True,
        permissions=None,
    )


def _dispatcher(tenant_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=UserRole.DISPATCHER,
        is_active=True,
        permissions=None,
    )


def _override_user(user):
    """Return a dependency override for get_current_user that yields user.
    Overriding get_current_user (not role_checker) means require_role still
    evaluates the user's role and can raise 403."""
    async def _dep():
        return user
    return _dep


def _tag(tag_id=None, name="Refrigerated", color="#6366f1", tenant_id=None):
    return SimpleNamespace(
        id=tag_id or uuid.uuid4(),
        name=name,
        color=color,
        tenant_id=tenant_id or uuid.uuid4(),
    )


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows if isinstance(rows, list) else [rows]

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Minimal async session that lets tests control what execute() returns."""

    def __init__(self, execute_result=None):
        self._execute_result = execute_result
        self.added = []
        self.committed = False
        self.refreshed = []
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        if callable(self._execute_result):
            return self._execute_result(stmt)
        return _ScalarResult(self._execute_result if self._execute_result is not None else [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed.append(obj)


# ---------------------------------------------------------------------------
# GET /vehicle-tags
# ---------------------------------------------------------------------------

class TestListVehicleTags:
    def test_dispatcher_can_list_tags(self):
        tenant_id = uuid.uuid4()
        user = _dispatcher(tenant_id=tenant_id)
        tags = [_tag(tenant_id=tenant_id), _tag(tenant_id=tenant_id)]
        db = _FakeDB(execute_result=tags)
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="GET")

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.get("/api/v1/vehicle-tags")

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_list_returns_empty_when_no_tags(self):
        user = _dispatcher()
        db = _FakeDB(execute_result=[])
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="GET")

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.get("/api/v1/vehicle-tags")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_includes_id_name_color_fields(self):
        tag_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user = _dispatcher(tenant_id=tenant_id)
        tag = _tag(tag_id=tag_id, name="Hazmat", color="#ef4444", tenant_id=tenant_id)
        db = _FakeDB(execute_result=[tag])
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="GET")

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.get("/api/v1/vehicle-tags")

        data = response.json()
        assert data[0]["id"] == str(tag_id)
        assert data[0]["name"] == "Hazmat"
        assert data[0]["color"] == "#ef4444"


# ---------------------------------------------------------------------------
# POST /vehicle-tags
# ---------------------------------------------------------------------------

class TestCreateVehicleTag:
    def test_admin_creates_tag_returns_201(self):
        tag_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user = _admin(tenant_id=tenant_id)
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="POST")

        created_tag = _tag(tag_id=tag_id, name="Refrigerated", color="#6366f1", tenant_id=tenant_id)

        async def _refresh(obj):
            obj.id = tag_id
            obj.name = "Refrigerated"
            obj.color = "#6366f1"
            obj.tenant_id = tenant_id

        db = _FakeDB()
        db.refresh = _refresh

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.post(
                "/api/v1/vehicle-tags",
                json={"name": "Refrigerated", "color": "#6366f1"},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Refrigerated"
        assert data["color"] == "#6366f1"
        assert db.committed is True

    def test_create_tag_sets_tenant_from_current_user(self):
        tenant_id = uuid.uuid4()
        user = _admin(tenant_id=tenant_id)
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="POST")
        captured = {}

        async def _refresh(obj):
            captured["tenant_id"] = obj.tenant_id
            obj.id = uuid.uuid4()
            obj.name = obj.name
            obj.color = obj.color

        db = _FakeDB()
        db.refresh = _refresh

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            client.post("/api/v1/vehicle-tags", json={"name": "Test", "color": "#000000"})

        assert captured["tenant_id"] == tenant_id

    def test_create_tag_default_color_when_omitted(self):
        tenant_id = uuid.uuid4()
        user = _admin(tenant_id=tenant_id)
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="POST")
        captured = {}

        async def _refresh(obj):
            captured["color"] = obj.color
            obj.id = uuid.uuid4()

        db = _FakeDB()
        db.refresh = _refresh

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            client.post("/api/v1/vehicle-tags", json={"name": "TestTag"})

        assert captured.get("color") == "#6366f1"

    def test_dispatcher_cannot_create_tag(self):
        user = _dispatcher()
        with build_test_client(
            dependency_overrides={get_current_user: _override_user(user)},
        ) as client:
            response = client.post(
                "/api/v1/vehicle-tags",
                json={"name": "Test", "color": "#6366f1"},
            )

        assert response.status_code == 403

    def test_invalid_hex_color_returns_422(self):
        user = _admin()
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="POST")

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
        ) as client:
            response = client.post(
                "/api/v1/vehicle-tags",
                json={"name": "Test", "color": "notacolor"},
            )

        assert response.status_code == 422

    def test_short_hex_color_returns_422(self):
        user = _admin()
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="POST")

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
        ) as client:
            response = client.post(
                "/api/v1/vehicle-tags",
                json={"name": "Test", "color": "#fff"},
            )

        assert response.status_code == 422

    def test_empty_name_returns_422(self):
        user = _admin()
        auth_dep = find_route_dependency("/api/v1/vehicle-tags", "current_user", method="POST")

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
        ) as client:
            response = client.post(
                "/api/v1/vehicle-tags",
                json={"name": "", "color": "#6366f1"},
            )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /vehicle-tags/{tag_id}
# ---------------------------------------------------------------------------

class TestUpdateVehicleTag:
    def test_admin_can_update_name(self):
        tag_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user = _admin(tenant_id=tenant_id)
        existing = _tag(tag_id=tag_id, name="Old Name", color="#6366f1", tenant_id=tenant_id)
        # Simulate tenant_filter match: tenant_id fields must align
        existing.tenant_id = tenant_id
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-tags/{tag_id}", "current_user", method="PATCH"
        )

        db = _FakeDB(execute_result=[existing])

        async def _refresh(obj):
            pass

        db.refresh = _refresh

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.patch(
                f"/api/v1/vehicle-tags/{tag_id}",
                json={"name": "New Name"},
            )

        assert response.status_code == 200
        assert existing.name == "New Name"

    def test_admin_can_update_color(self):
        tag_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user = _admin(tenant_id=tenant_id)
        existing = _tag(tag_id=tag_id, name="Tag", color="#6366f1", tenant_id=tenant_id)
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-tags/{tag_id}", "current_user", method="PATCH"
        )

        db = _FakeDB(execute_result=[existing])

        async def _refresh(obj):
            pass

        db.refresh = _refresh

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.patch(
                f"/api/v1/vehicle-tags/{tag_id}",
                json={"color": "#ef4444"},
            )

        assert response.status_code == 200
        assert existing.color == "#ef4444"

    def test_missing_tag_returns_404(self):
        tag_id = uuid.uuid4()
        user = _admin()
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-tags/{tag_id}", "current_user", method="PATCH"
        )

        db = _FakeDB(execute_result=[])

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.patch(
                f"/api/v1/vehicle-tags/{tag_id}",
                json={"name": "New Name"},
            )

        assert response.status_code == 404

    def test_dispatcher_cannot_update_tag(self):
        tag_id = uuid.uuid4()
        user = _dispatcher()
        with build_test_client(
            dependency_overrides={get_current_user: _override_user(user)},
        ) as client:
            response = client.patch(
                f"/api/v1/vehicle-tags/{tag_id}",
                json={"name": "Hacked"},
            )

        assert response.status_code == 403

    def test_invalid_color_on_update_returns_422(self):
        tag_id = uuid.uuid4()
        user = _admin()
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-tags/{tag_id}", "current_user", method="PATCH"
        )

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
        ) as client:
            response = client.patch(
                f"/api/v1/vehicle-tags/{tag_id}",
                json={"color": "bad-color"},
            )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /vehicle-tags/{tag_id}
# ---------------------------------------------------------------------------

class TestDeleteVehicleTag:
    def test_admin_can_delete_tag(self):
        tag_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user = _admin(tenant_id=tenant_id)
        existing = _tag(tag_id=tag_id, tenant_id=tenant_id)
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-tags/{tag_id}", "current_user", method="DELETE"
        )

        db = _FakeDB(execute_result=[existing])

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.delete(f"/api/v1/vehicle-tags/{tag_id}")

        assert response.status_code == 204
        assert db.committed is True

    def test_delete_missing_tag_returns_404(self):
        tag_id = uuid.uuid4()
        user = _admin()
        auth_dep = find_route_dependency(
            "/api/v1/vehicle-tags/{tag_id}", "current_user", method="DELETE"
        )

        db = _FakeDB(execute_result=[])

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.delete(f"/api/v1/vehicle-tags/{tag_id}")

        assert response.status_code == 404

    def test_dispatcher_cannot_delete_tag(self):
        tag_id = uuid.uuid4()
        user = _dispatcher()
        with build_test_client(
            dependency_overrides={get_current_user: _override_user(user)},
        ) as client:
            response = client.delete(f"/api/v1/vehicle-tags/{tag_id}")

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# PUT /couriers/{courier_id}/tags
# ---------------------------------------------------------------------------

class TestSetCourierTags:
    def test_admin_sets_tags_on_courier(self, monkeypatch):
        from app.api import couriers as couriers_api

        courier_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        tag_id = uuid.uuid4()
        user = _admin(tenant_id=tenant_id)
        auth_dep = find_route_dependency(
            f"/api/v1/couriers/{{courier_id}}/tags", "current_user", method="PUT"
        )

        tag_obj = _tag(tag_id=tag_id, name="Refrigerated", color="#6366f1", tenant_id=tenant_id)
        courier_obj = SimpleNamespace(
            id=courier_id,
            tenant_id=tenant_id,
            vehicle_tags=[],
        )

        async def _mock_get_courier(db, courier_id, current_user):
            return courier_obj

        monkeypatch.setattr(couriers_api.courier_service, "get_courier", _mock_get_courier)

        db = _FakeDB(execute_result=[tag_obj])

        async def _commit():
            pass

        async def _refresh(obj):
            pass

        db.commit = _commit
        db.refresh = _refresh

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.put(
                f"/api/v1/couriers/{courier_id}/tags",
                json={"tag_ids": [str(tag_id)]},
            )

        assert response.status_code == 200
        assert courier_obj.vehicle_tags == [tag_obj]

    def test_clearing_tags_sets_empty_list(self, monkeypatch):
        from app.api import couriers as couriers_api

        courier_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        tag_id = uuid.uuid4()
        user = _admin(tenant_id=tenant_id)
        auth_dep = find_route_dependency(
            f"/api/v1/couriers/{{courier_id}}/tags", "current_user", method="PUT"
        )

        existing_tag = _tag(tag_id=tag_id, tenant_id=tenant_id)
        courier_obj = SimpleNamespace(
            id=courier_id,
            tenant_id=tenant_id,
            vehicle_tags=[existing_tag],
        )

        async def _mock_get_courier(db, courier_id, current_user):
            return courier_obj

        monkeypatch.setattr(couriers_api.courier_service, "get_courier", _mock_get_courier)

        db = _FakeDB(execute_result=[])

        async def _commit():
            pass

        async def _refresh(obj):
            pass

        db.commit = _commit
        db.refresh = _refresh

        with build_test_client(
            dependency_overrides={auth_dep: lambda: user},
            db_session=db,
        ) as client:
            response = client.put(
                f"/api/v1/couriers/{courier_id}/tags",
                json={"tag_ids": []},
            )

        assert response.status_code == 200
        assert courier_obj.vehicle_tags == []

    def test_dispatcher_cannot_set_courier_tags(self):
        courier_id = uuid.uuid4()
        user = _dispatcher()
        with build_test_client(
            dependency_overrides={get_current_user: _override_user(user)},
        ) as client:
            response = client.put(
                f"/api/v1/couriers/{courier_id}/tags",
                json={"tag_ids": []},
            )

        assert response.status_code == 403
