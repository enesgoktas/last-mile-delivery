import uuid
from datetime import timedelta
from types import SimpleNamespace

from app.utils.compat import utcnow_naive
from tests.integration.test_utils import build_test_client, find_route_dependency


def _route_response(route_id: uuid.UUID, courier_id: uuid.UUID) -> dict:
    now = utcnow_naive()
    return {
        "id": str(route_id),
        "courier_id": str(courier_id),
        "status": "ACTIVE",
        "planned_distance_km": 12.4,
        "actual_distance_km": None,
        "optimization_run_id": None,
        "created_at": now.isoformat(),
        "started_at": now.isoformat(),
        "completed_at": None,
        "stops": [],
    }


def _stop_response(stop_id: uuid.UUID, order_id: uuid.UUID, status: str) -> dict:
    now = utcnow_naive()
    return {
        "id": str(stop_id),
        "order_id": str(order_id),
        "sequence_number": 3,
        "stop_type": "DELIVERY",
        "lat": 41.02,
        "lon": 29.03,
        "address": "Customer Address",
        "planned_arrival": (now + timedelta(minutes=10)).isoformat(),
        "actual_arrival": now.isoformat() if status in {"ARRIVED", "COMPLETED", "FAILED"} else None,
        "status": status,
        "failure_reason": "NOT_HOME" if status == "FAILED" else None,
        "notes": "customer unavailable" if status == "FAILED" else "completed successfully",
    }


def _courier_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role="COURIER",
        is_active=True,
    )


def test_route_start_endpoint_calls_service(monkeypatch):
    from app.api import routes as routes_api

    auth_dep = find_route_dependency("/api/v1/routes/{route_id}/start", "current_user")

    async def _override_current_user():
        return _courier_user()

    route_id = uuid.uuid4()
    courier_id = uuid.uuid4()
    captured = {"route_id": None}

    async def _mock_start_route(_db, requested_route_id, _current_user):
        captured["route_id"] = requested_route_id
        return _route_response(requested_route_id, courier_id)

    monkeypatch.setattr(routes_api.route_execution_service, "start_route", _mock_start_route)
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.post(f"/api/v1/routes/{route_id}/start")

    assert response.status_code == 200
    assert response.json()["id"] == str(route_id)
    assert response.json()["status"] == "ACTIVE"
    assert captured["route_id"] == route_id


def test_route_arrive_endpoint_calls_service(monkeypatch):
    from app.api import routes as routes_api

    auth_dep = find_route_dependency("/api/v1/routes/{route_id}/stops/{stop_id}/arrive", "current_user")

    async def _override_current_user():
        return _courier_user()

    route_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    order_id = uuid.uuid4()
    captured = {"route_id": None, "stop_id": None}

    async def _mock_arrive(_db, requested_route_id, requested_stop_id, _current_user):
        captured["route_id"] = requested_route_id
        captured["stop_id"] = requested_stop_id
        return _stop_response(requested_stop_id, order_id, "ARRIVED")

    monkeypatch.setattr(routes_api.route_execution_service, "arrive_at_stop", _mock_arrive)
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.post(f"/api/v1/routes/{route_id}/stops/{stop_id}/arrive")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(stop_id)
    assert body["status"] == "ARRIVED"
    assert captured["route_id"] == route_id
    assert captured["stop_id"] == stop_id


def test_route_complete_endpoint_forwards_pod_payload(monkeypatch):
    from app.api import routes as routes_api

    auth_dep = find_route_dependency("/api/v1/routes/{route_id}/stops/{stop_id}/complete", "current_user")

    async def _override_current_user():
        return _courier_user()

    route_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    order_id = uuid.uuid4()
    captured = {
        "photo_urls": None,
        "signature_url": None,
        "receiver_name": None,
        "otp_code": None,
        "notes": None,
    }

    async def _mock_complete(
        _db,
        _route_id,
        requested_stop_id,
        _current_user,
        *,
        photo_urls,
        photo_url,
        signature_url,
        receiver_name,
        otp_code,
        pod_notes,
    ):
        captured["photo_urls"] = photo_urls or ([photo_url] if photo_url else None)
        captured["signature_url"] = signature_url
        captured["receiver_name"] = receiver_name
        captured["otp_code"] = otp_code
        captured["notes"] = pod_notes
        return _stop_response(requested_stop_id, order_id, "COMPLETED")

    monkeypatch.setattr(routes_api.route_execution_service, "complete_stop", _mock_complete)
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.post(
            f"/api/v1/routes/{route_id}/stops/{stop_id}/complete",
            json={
                "photo_urls": [
                    "https://cdn.example.com/photo.jpg",
                    "https://cdn.example.com/photo-2.jpg",
                ],
                "signature_url": "https://cdn.example.com/signature.jpg",
                "receiver_name": "Alex",
                "otp_code": "123456",
                "notes": "left with doorman",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert captured["photo_urls"] == [
        "https://cdn.example.com/photo.jpg",
        "https://cdn.example.com/photo-2.jpg",
    ]
    assert captured["signature_url"] == "https://cdn.example.com/signature.jpg"
    assert captured["receiver_name"] == "Alex"
    assert captured["otp_code"] == "123456"
    assert captured["notes"] == "left with doorman"


def test_route_fail_endpoint_forwards_failure_reason(monkeypatch):
    from app.api import routes as routes_api

    auth_dep = find_route_dependency("/api/v1/routes/{route_id}/stops/{stop_id}/fail", "current_user")

    async def _override_current_user():
        return _courier_user()

    route_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    order_id = uuid.uuid4()
    captured = {"reason": None, "notes": None, "custom_reason_label": None}

    async def _mock_fail(
        _db,
        _route_id,
        requested_stop_id,
        _current_user,
        failure_reason,
        custom_reason_label=None,
        notes=None,
    ):
        captured["reason"] = failure_reason
        captured["notes"] = notes
        captured["custom_reason_label"] = custom_reason_label
        return _stop_response(requested_stop_id, order_id, "FAILED")

    monkeypatch.setattr(routes_api.route_execution_service, "fail_stop", _mock_fail)
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.post(
            f"/api/v1/routes/{route_id}/stops/{stop_id}/fail",
            json={
                "failure_reason": "OTHER",
                "notes": "customer unavailable",
                "custom_reason_label": "Gate locked",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "FAILED"
    assert str(captured["reason"]) in {"FailureReason.OTHER", "OTHER"}
    assert captured["notes"] == "customer unavailable"
    assert captured["custom_reason_label"] == "Gate locked"


def test_route_pod_upload_url_endpoint_returns_contract(monkeypatch):
    from app.api import routes as routes_api

    auth_dep = find_route_dependency("/api/v1/routes/{route_id}/stops/{stop_id}/pod/upload-url", "current_user")

    async def _override_current_user():
        return _courier_user()

    route_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    expires_at = utcnow_naive() + timedelta(minutes=15)
    captured = {"file_type": None, "content_type": None}

    async def _mock_upload_url(
        _db,
        *,
        route_id,
        stop_id,
        current_user,
        file_type,
        content_type,
    ):
        captured["file_type"] = file_type
        captured["content_type"] = content_type
        return {
            "upload_url": "https://s3.example.com/presigned",
            "file_url": "https://cdn.example.com/pod/file.jpg",
            "object_key": "pod/obj-123",
            "expires_at": expires_at,
        }

    monkeypatch.setattr(routes_api.route_execution_service, "create_pod_upload_url", _mock_upload_url)
    with build_test_client(dependency_overrides={auth_dep: _override_current_user}) as client:
        response = client.post(
            f"/api/v1/routes/{route_id}/stops/{stop_id}/pod/upload-url",
            json={"file_type": "photo", "content_type": "image/jpeg"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["upload_url"] == "https://s3.example.com/presigned"
    assert body["file_url"] == "https://cdn.example.com/pod/file.jpg"
    assert body["object_key"] == "pod/obj-123"
    assert body["expires_at"] is not None
    assert captured["file_type"] == "photo"
    assert captured["content_type"] == "image/jpeg"


def test_route_geometry_endpoint_returns_geojson_payload(monkeypatch):
    from app.api import routes as routes_api

    role_dep = find_route_dependency("/api/v1/routes/{route_id}/geometry", "_user")

    user = SimpleNamespace(
        id=uuid.uuid4(),
        role="DISPATCHER",
        is_active=True,
        tenant_id=uuid.uuid4(),
    )

    async def _override_dispatcher_user():
        return user

    route_id = uuid.uuid4()

    async def _mock_geometry(_db, requested_route_id, current_user):
        assert requested_route_id == route_id
        assert current_user is user
        return {
            "route_id": route_id,
            "status": "ACTIVE",
            "source": "generated",
            "generated_at": utcnow_naive(),
            "geometry": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[29.0, 41.0], [29.01, 41.01]],
                        },
                        "properties": {"kind": "full_route"},
                    }
                ],
            },
        }

    monkeypatch.setattr(routes_api.route_service, "get_route_geometry", _mock_geometry)
    with build_test_client(dependency_overrides={role_dep: _override_dispatcher_user}) as client:
        response = client.get(f"/api/v1/routes/{route_id}/geometry")

    assert response.status_code == 200
    body = response.json()
    assert body["route_id"] == str(route_id)
    assert body["geometry"]["type"] == "FeatureCollection"
    assert body["geometry"]["features"]
