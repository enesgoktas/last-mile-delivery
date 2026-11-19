"""End-to-end coverage of the Phase 1 multi-depot courier API.

Exercises the per-tenant `multi_depot_vehicles_enabled` feature flag gate:
  - Flag off  → POST / PUT with multi-depot fields are rejected (400).
  - Flag on   → fields persist, returned in GET, and validated against the
                allowed-depot set (`end_depot_id ∈ {depot_id} ∪ additional`).

Phase 1 is scaffolding only — the solver still ignores these fields, so
this file does not assert any routing behavior. That comes in Phase 2+.
"""
from __future__ import annotations

import uuid

from app.models.enums import UserRole
from tests.integration.live_api_helpers import (
    auth_headers,
    register_user,
    signup_owner,
)


def _create_depot(client, *, access_token: str, name: str, lat: float, lon: float) -> dict:
    response = client.post(
        "/api/v1/depots",
        headers=auth_headers(access_token),
        json={
            "name": name,
            "address": f"{name} address",
            "lat": lat,
            "lon": lon,
            "operating_hours_start": "08:00:00",
            "operating_hours_end": "20:00:00",
            "is_active": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _enable_multi_depot_flag(client, *, access_token: str) -> None:
    response = client.patch(
        "/api/v1/settings/operations-policy",
        headers=auth_headers(access_token),
        json={"feature_flags": {"multi_depot_vehicles_enabled": True}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["feature_flags"]["multi_depot_vehicles_enabled"] is True


def _register_driver_user(client, *, access_token: str) -> str:
    email = f"driver-{uuid.uuid4().hex[:10]}@example.com"
    user = register_user(
        client,
        access_token=access_token,
        email=email,
        password="Password1!",
        role=UserRole.COURIER,
    )
    return user["id"]


def _courier_payload(user_id: str, depot_id: str, **extra) -> dict:
    payload = {
        "user_id": user_id,
        "vehicle_type": "CAR_VAN",
        "max_weight_kg": 100.0,
        "depot_lat": 41.0082,
        "depot_lon": 28.9784,
        "depot_id": depot_id,
        "working_hours_start": "08:00:00",
        "working_hours_end": "20:00:00",
    }
    payload.update(extra)
    return payload


def test_multi_depot_fields_rejected_when_flag_off(live_test_client):
    """With the feature flag default-off, the API must reject the new fields."""
    owner = signup_owner(live_test_client, label="owner-flagoff")
    access_token = owner["access_token"]

    home = _create_depot(
        live_test_client,
        access_token=access_token,
        name="Home",
        lat=41.0082,
        lon=28.9784,
    )
    other = _create_depot(
        live_test_client,
        access_token=access_token,
        name="Satellite",
        lat=41.05,
        lon=28.99,
    )
    user_id = _register_driver_user(live_test_client, access_token=access_token)

    response = live_test_client.post(
        "/api/v1/couriers",
        headers=auth_headers(access_token),
        json=_courier_payload(
            user_id,
            home["id"],
            additional_depot_ids=[other["id"]],
        ),
    )
    assert response.status_code == 400
    assert "multi_depot_vehicles_enabled" in response.json()["detail"]


def test_multi_depot_create_and_update_round_trip(live_test_client):
    """With the flag on, the API persists, returns, and validates the new fields."""
    owner = signup_owner(live_test_client, label="owner-flagon")
    access_token = owner["access_token"]

    _enable_multi_depot_flag(live_test_client, access_token=access_token)

    home = _create_depot(
        live_test_client,
        access_token=access_token,
        name="Home",
        lat=41.0082,
        lon=28.9784,
    )
    satellite = _create_depot(
        live_test_client,
        access_token=access_token,
        name="Satellite",
        lat=41.05,
        lon=28.99,
    )
    other = _create_depot(
        live_test_client,
        access_token=access_token,
        name="Other",
        lat=41.10,
        lon=29.05,
    )
    user_id = _register_driver_user(live_test_client, access_token=access_token)

    # Create courier with one additional depot + end_depot_id pointing at it.
    create_response = live_test_client.post(
        "/api/v1/couriers",
        headers=auth_headers(access_token),
        json=_courier_payload(
            user_id,
            home["id"],
            additional_depot_ids=[satellite["id"]],
            end_depot_id=satellite["id"],
        ),
    )
    assert create_response.status_code == 201, create_response.text
    courier = create_response.json()
    courier_id = courier["id"]
    assert set(courier["additional_depot_ids"]) == {satellite["id"]}
    assert courier["end_depot_id"] == satellite["id"]

    # GET round-trip preserves the fields.
    get_response = live_test_client.get(
        f"/api/v1/couriers/{courier_id}",
        headers=auth_headers(access_token),
    )
    assert get_response.status_code == 200
    assert set(get_response.json()["additional_depot_ids"]) == {satellite["id"]}

    # end_depot_id must be in the allowed set — `other` is not, so 400.
    bad_response = live_test_client.put(
        f"/api/v1/couriers/{courier_id}",
        headers=auth_headers(access_token),
        json={"end_depot_id": other["id"]},
    )
    assert bad_response.status_code == 400
    assert "end_depot_id" in bad_response.json()["detail"]

    # Expand the allowed set to include `other`, then setting end_depot_id is OK.
    update_response = live_test_client.put(
        f"/api/v1/couriers/{courier_id}",
        headers=auth_headers(access_token),
        json={
            "additional_depot_ids": [satellite["id"], other["id"]],
            "end_depot_id": other["id"],
        },
    )
    assert update_response.status_code == 200, update_response.text
    updated = update_response.json()
    assert set(updated["additional_depot_ids"]) == {satellite["id"], other["id"]}
    assert updated["end_depot_id"] == other["id"]

    # Clearing additional_depot_ids back to [] also clears the end_depot_id
    # implication: we explicitly null end_depot_id in the same payload.
    clear_response = live_test_client.put(
        f"/api/v1/couriers/{courier_id}",
        headers=auth_headers(access_token),
        json={"additional_depot_ids": [], "end_depot_id": None},
    )
    assert clear_response.status_code == 200
    cleared = clear_response.json()
    assert cleared["additional_depot_ids"] == []
    assert cleared["end_depot_id"] is None


def test_cross_tenant_depot_ids_rejected(live_test_client):
    """A depot belonging to another tenant must not leak into a courier's allowed set."""
    owner_a = signup_owner(live_test_client, label="owner-a")
    owner_b = signup_owner(live_test_client, label="owner-b")
    token_a = owner_a["access_token"]
    token_b = owner_b["access_token"]

    _enable_multi_depot_flag(live_test_client, access_token=token_a)
    _enable_multi_depot_flag(live_test_client, access_token=token_b)

    home_a = _create_depot(
        live_test_client,
        access_token=token_a,
        name="A-Home",
        lat=41.0082,
        lon=28.9784,
    )
    foreign_b = _create_depot(
        live_test_client,
        access_token=token_b,
        name="B-Home",
        lat=39.9,
        lon=32.8,
    )
    driver_a_id = _register_driver_user(live_test_client, access_token=token_a)

    response = live_test_client.post(
        "/api/v1/couriers",
        headers=auth_headers(token_a),
        json=_courier_payload(
            driver_a_id,
            home_a["id"],
            additional_depot_ids=[foreign_b["id"]],
        ),
    )
    assert response.status_code == 400
    assert "additional_depot_ids" in response.json()["detail"]
