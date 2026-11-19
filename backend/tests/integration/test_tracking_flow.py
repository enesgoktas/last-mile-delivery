from datetime import datetime, timedelta

from app.config import settings
from app.utils.compat import UTC
from tests.integration.test_utils import build_test_client


def _tracking_payload(status: str = "IN_TRANSIT") -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    return {
        "order_id": "ord_123",
        "status": status,
        "delivery_address": "Customer Address",
        "delivery_lat": 41.01,
        "delivery_lon": 29.01,
        "route_id": "route_1",
        "courier_lat": 41.0,
        "courier_lon": 29.0,
        "estimated_arrival": (now + timedelta(minutes=25)).isoformat(),
        "last_updated": now.isoformat(),
        "eta_source": "route_sequence",
        "total_stops": 5,
        "completed_stops": 2,
        "remaining_stops": 3,
        "next_stop_sequence": 3,
        "target_stop_sequence": 5,
    }


def test_get_tracking_returns_tracking_payload(monkeypatch):
    from app.api import tracking as tracking_api

    calls = {"token": None}

    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _mock_get_tracking_data(_db, token: str):
        calls["token"] = token
        return _tracking_payload()

    monkeypatch.setattr(tracking_api, "enforce_fixed_window_limit", _noop_rate_limit)
    monkeypatch.setattr(tracking_api.tracking_service, "get_tracking_data", _mock_get_tracking_data)
    with build_test_client() as client:
        response = client.get("/api/v1/tracking/tok_abc")

    assert response.status_code == 200
    assert response.json()["order_id"] == "ord_123"
    assert response.json()["status"] == "IN_TRANSIT"
    assert calls["token"] == "tok_abc"


def test_get_tracking_uses_configured_rate_limit(monkeypatch):
    from app.api import tracking as tracking_api

    captured: dict[str, object] = {}

    async def _capture_rate_limit(**kwargs):
        captured.update(kwargs)

    async def _mock_get_tracking_data(_db, _token: str):
        return _tracking_payload()

    monkeypatch.setattr(tracking_api, "enforce_fixed_window_limit", _capture_rate_limit)
    monkeypatch.setattr(tracking_api.tracking_service, "get_tracking_data", _mock_get_tracking_data)

    with build_test_client() as client:
        response = client.get("/api/v1/tracking/tok_rate")

    assert response.status_code == 200
    assert captured["limit"] == settings.PUBLIC_TRACKING_READ_RATE_LIMIT_PER_MIN
    assert captured["window_seconds"] == 60
    assert str(captured["key"]).startswith("rl:tracking:read:")


def test_reschedule_with_token_calls_service_and_returns_fresh_tracking(monkeypatch):
    from app.api import tracking as tracking_api

    calls = {"token": None, "earliest": None, "latest": None}

    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _mock_reschedule(
        _db,
        token: str,
        *,
        earliest_delivery,
        latest_delivery,
        customer_instructions=None,
    ):
        calls["token"] = token
        calls["earliest"] = earliest_delivery
        calls["latest"] = latest_delivery
        return None

    async def _mock_get_tracking_data(_db, token: str):
        assert token == "tok_xyz"
        return _tracking_payload(status="RESCHEDULED")

    monkeypatch.setattr(
        tracking_api.tracking_service,
        "reschedule_by_tracking_token",
        _mock_reschedule,
    )
    monkeypatch.setattr(tracking_api, "enforce_fixed_window_limit", _noop_rate_limit)
    monkeypatch.setattr(tracking_api.tracking_service, "get_tracking_data", _mock_get_tracking_data)

    earliest = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=3)
    latest = earliest + timedelta(hours=2)

    with build_test_client() as client:
        response = client.post(
            "/api/v1/tracking/tok_xyz/reschedule",
            json={
                "earliest_delivery": earliest.isoformat(),
                "latest_delivery": latest.isoformat(),
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "RESCHEDULED"
    assert calls["token"] == "tok_xyz"
    assert calls["earliest"] == earliest
    assert calls["latest"] == latest


def test_reschedule_with_token_uses_configured_rate_limit(monkeypatch):
    from app.api import tracking as tracking_api

    captured: dict[str, object] = {}

    async def _capture_rate_limit(**kwargs):
        captured.update(kwargs)

    async def _mock_reschedule(
        _db,
        _token: str,
        *,
        earliest_delivery,
        latest_delivery,
        customer_instructions=None,
    ):
        assert customer_instructions is None
        assert earliest_delivery is not None
        assert latest_delivery is not None
        return None

    async def _mock_get_tracking_data(_db, _token: str):
        return _tracking_payload(status="RESCHEDULED")

    monkeypatch.setattr(tracking_api, "enforce_fixed_window_limit", _capture_rate_limit)
    monkeypatch.setattr(
        tracking_api.tracking_service,
        "reschedule_by_tracking_token",
        _mock_reschedule,
    )
    monkeypatch.setattr(tracking_api.tracking_service, "get_tracking_data", _mock_get_tracking_data)

    earliest = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=3)
    latest = earliest + timedelta(hours=2)

    with build_test_client() as client:
        response = client.post(
            "/api/v1/tracking/tok_rate/reschedule",
            json={
                "earliest_delivery": earliest.isoformat(),
                "latest_delivery": latest.isoformat(),
            },
        )

    assert response.status_code == 200
    assert captured["limit"] == settings.PUBLIC_TRACKING_WRITE_RATE_LIMIT_PER_HOUR
    assert captured["window_seconds"] == 3600
    assert str(captured["key"]).startswith("rl:tracking:write:")
