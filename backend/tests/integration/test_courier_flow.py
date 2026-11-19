import uuid
from datetime import time as dt_time
from types import SimpleNamespace

from fastapi import HTTPException

from tests.integration.test_utils import build_test_client, find_route_dependency


def _courier_response(courier_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    return {
        "id": str(courier_id),
        "user_id": str(user_id),
        "vehicle_type": "MOTORCYCLE",
        "max_weight_kg": 30.0,
        "availability_status": "ONLINE",
        "zone_polygon": None,
        "depot_lat": 41.0,
        "depot_lon": 29.0,
        "working_hours_start": dt_time(9, 0).isoformat(),
        "working_hours_end": dt_time(18, 0).isoformat(),
        "fcm_token": None,
        "push_provider": None,
        "push_token": None,
    }


def test_courier_location_update_calls_access_check_and_service(monkeypatch):
    from app.api import couriers as couriers_api

    auth_dep = find_route_dependency("/api/v1/couriers/{courier_id}/location", "current_user")
    tenant_id = uuid.uuid4()

    async def _override_current_user():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="COURIER", is_active=True)

    captured = {"checked": False, "updated": False}
    courier_id = uuid.uuid4()

    async def _mock_check_access(_db, current_user, target_courier_id):
        assert current_user.role == "COURIER"
        assert target_courier_id == courier_id
        captured["checked"] = True

    async def _mock_update_location(_db, target_courier_id, location_data):
        assert target_courier_id == courier_id
        assert location_data.lat == 41.05
        assert location_data.lon == 29.02
        captured["updated"] = True
        return {"status": "ok", "geofence_status": "enqueued", "geofence_result": None}

    monkeypatch.setattr(couriers_api.courier_service, "ensure_user_can_access_courier", _mock_check_access)
    monkeypatch.setattr(couriers_api.courier_service, "update_location", _mock_update_location)
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.put(
            f"/api/v1/couriers/{courier_id}/location",
            json={
                "lat": 41.05,
                "lon": 29.02,
                "heading": 180.0,
                "speed_kmh": 22.0,
                "accuracy_m": 6.0,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert captured["checked"] is True
    assert captured["updated"] is True


def test_courier_status_update_propagates_forbidden_from_access_guard(monkeypatch):
    from app.api import couriers as couriers_api

    auth_dep = find_route_dependency("/api/v1/couriers/{courier_id}/status", "current_user")
    tenant_id = uuid.uuid4()

    async def _override_current_user():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="COURIER", is_active=True)

    courier_id = uuid.uuid4()

    async def _mock_forbidden(_db, _current_user, _target_courier_id):
        raise HTTPException(status_code=403, detail="Couriers can only access their own profile")

    async def _should_not_update(*_args, **_kwargs):
        raise AssertionError("update_status should not be called when access check fails")

    monkeypatch.setattr(couriers_api.courier_service, "ensure_user_can_access_courier", _mock_forbidden)
    monkeypatch.setattr(couriers_api.courier_service, "update_status", _should_not_update)
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.put(
            f"/api/v1/couriers/{courier_id}/status",
            json={"status": "OFFLINE"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Couriers can only access their own profile"


def test_courier_push_token_update_returns_updated_profile(monkeypatch):
    from app.api import couriers as couriers_api

    auth_dep = find_route_dependency("/api/v1/couriers/{courier_id}/push-token", "current_user")
    tenant_id = uuid.uuid4()

    async def _override_current_user():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="COURIER", is_active=True)

    courier_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def _mock_check_access(_db, _current_user, _target_courier_id):
        return None

    async def _mock_update_push_token(_db, target_courier_id, *, push_provider, push_token, fcm_token):
        assert target_courier_id == courier_id
        assert push_provider == "EXPO"
        assert push_token == "ExponentPushToken[token-123]"
        assert fcm_token is None
        return _courier_response(courier_id, user_id) | {
            "push_provider": push_provider,
            "push_token": push_token,
            "fcm_token": None,
        }

    monkeypatch.setattr(couriers_api.courier_service, "ensure_user_can_access_courier", _mock_check_access)
    monkeypatch.setattr(couriers_api.courier_service, "update_push_token", _mock_update_push_token)
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.put(
            f"/api/v1/couriers/{courier_id}/push-token",
            json={
                "push_provider": "EXPO",
                "push_token": "ExponentPushToken[token-123]",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(courier_id)
    assert body["push_provider"] == "EXPO"
    assert body["push_token"] == "ExponentPushToken[token-123]"
    assert body["fcm_token"] is None


def test_get_my_courier_profile_returns_current_users_profile(monkeypatch):
    from app.api import couriers as couriers_api

    auth_dep = find_route_dependency("/api/v1/couriers/me", "current_user")
    user_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    async def _override_current_user():
        return SimpleNamespace(id=user_id, tenant_id=tenant_id, role="COURIER", is_active=True)

    async def _mock_get_for_user(_db, user):
        assert user.id == user_id
        return _courier_response(courier_id, user_id)

    monkeypatch.setattr(
        couriers_api.courier_service,
        "get_courier_for_user_or_404",
        _mock_get_for_user,
    )
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.get("/api/v1/couriers/me")

    assert response.status_code == 200
    assert response.json()["id"] == str(courier_id)


def test_list_latest_courier_locations_returns_dispatcher_payload(monkeypatch):
    from app.api import couriers as couriers_api

    role_dep = find_route_dependency("/api/v1/couriers/locations", "_user")
    user_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    async def _override_dispatcher_user():
        return SimpleNamespace(id=user_id, tenant_id=tenant_id, role="DISPATCHER", is_active=True)

    async def _mock_locations(_db, current_user):
        assert current_user.tenant_id == tenant_id
        return [
            {
                "courier_id": courier_id,
                "vehicle_type": "CAR_VAN",
                "availability_status": "ONLINE",
                "lat": 41.01,
                "lon": 29.02,
                "heading": 180.0,
                "speed_kmh": 28.5,
                "updated_at": "2026-02-25T09:00:00",
                "source": "redis",
            }
        ]

    monkeypatch.setattr(couriers_api.courier_service, "list_latest_locations_for_dispatcher", _mock_locations)
    with build_test_client(dependency_overrides={role_dep: _override_dispatcher_user}) as client:
        response = client.get("/api/v1/couriers/locations")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["courier_id"] == str(courier_id)
    assert body[0]["source"] == "redis"
