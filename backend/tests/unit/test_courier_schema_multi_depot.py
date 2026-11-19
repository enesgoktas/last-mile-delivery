"""Pydantic schema-level coverage of the multi-depot courier fields.

Verifies that:
  - The new fields default to None (back-compat — payloads without them
    must continue to validate).
  - When supplied, the fields are typed as list[UUID] / UUID and round-trip
    cleanly through Pydantic.
"""
from __future__ import annotations

import uuid
from datetime import time as dt_time

from app.schemas.courier import CourierCreate, CourierResponse, CourierUpdate
from app.schemas.operations_policy import (
    FEATURE_FLAG_SCHEMA,
    FeatureFlags,
)


def _base_create_payload() -> dict:
    return {
        "user_id": str(uuid.uuid4()),
        "vehicle_type": "CAR_VAN",
        "max_weight_kg": 100.0,
        "depot_lat": 41.0,
        "depot_lon": 29.0,
        "working_hours_start": dt_time(8, 0),
        "working_hours_end": dt_time(20, 0),
    }


def test_courier_create_defaults_preserve_back_compat():
    payload = _base_create_payload()
    model = CourierCreate.model_validate(payload)
    assert model.additional_depot_ids is None
    assert model.end_depot_id is None


def test_courier_create_accepts_multi_depot_fields():
    additional = [uuid.uuid4(), uuid.uuid4()]
    end = additional[0]
    payload = _base_create_payload()
    payload["additional_depot_ids"] = [str(d) for d in additional]
    payload["end_depot_id"] = str(end)

    model = CourierCreate.model_validate(payload)
    assert model.additional_depot_ids == additional
    assert model.end_depot_id == end


def test_courier_update_defaults_omit_multi_depot_fields():
    update = CourierUpdate.model_validate({})
    dumped = update.model_dump(exclude_unset=True)
    # When the caller doesn't touch the new fields, they must not appear
    # in the dump — that's how the service knows to leave them alone.
    assert "additional_depot_ids" not in dumped
    assert "end_depot_id" not in dumped


def test_courier_update_distinguishes_unset_from_empty_list():
    update = CourierUpdate.model_validate({"additional_depot_ids": []})
    dumped = update.model_dump(exclude_unset=True)
    # Empty list IS in the dump — it means "clear the additional depots".
    assert dumped["additional_depot_ids"] == []


def test_courier_response_round_trip_with_multi_depot_fields():
    additional = [uuid.uuid4()]
    end = additional[0]
    response = CourierResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "vehicle_type": "CAR_VAN",
            "max_weight_kg": 100.0,
            "availability_status": "OFFLINE",
            "zone_polygon": None,
            "depot_lat": 41.0,
            "depot_lon": 29.0,
            "depot_id": None,
            "working_hours_start": dt_time(8, 0),
            "working_hours_end": dt_time(20, 0),
            "fcm_token": None,
            "additional_depot_ids": additional,
            "end_depot_id": end,
        }
    )
    assert response.additional_depot_ids == additional
    assert response.end_depot_id == end


def test_feature_flag_default_off_and_listed_in_schema():
    flags = FeatureFlags()
    assert flags.multi_depot_vehicles_enabled is False

    names = [entry["name"] for entry in FEATURE_FLAG_SCHEMA]
    assert "multi_depot_vehicles_enabled" in names

    entry = next(e for e in FEATURE_FLAG_SCHEMA if e["name"] == "multi_depot_vehicles_enabled")
    assert entry["default"] is False
    assert entry["category"] == "Routing"
    assert entry["depends_on"] == []
