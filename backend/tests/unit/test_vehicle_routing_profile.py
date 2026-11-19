"""
Unit tests for vehicle_routing_profile.resolve_osrm_profile_from_values.

Verifies that:
  - Motorcycles always get the motorcycle profile
  - Heavy vehicles (>= 3500 kg) get the truck profile
  - VehicleType.TRUCK always gets the truck profile
  - Everything else gets the driving profile
  - An explicit osrm_profile on VehicleSpec overrides all logic
  - SimpleNamespace (test mock) objects work without AttributeError
"""
from types import SimpleNamespace

import pytest

from app.models.enums import VehicleType
from app.services.vehicle_routing_profile import resolve_osrm_profile_from_values


@pytest.mark.parametrize("vehicle_type,gross_weight_kg,expected_profile", [
    # Motorcycles always → motorcycle
    (VehicleType.MOTORCYCLE, None,  "motorcycle"),
    (VehicleType.MOTORCYCLE, 500,   "motorcycle"),
    (VehicleType.MOTORCYCLE, 12000, "motorcycle"),

    # VehicleType.TRUCK always → truck
    (VehicleType.TRUCK, None,  "truck"),
    (VehicleType.TRUCK, 1000,  "truck"),

    # >= 3500 kg → truck regardless of type
    (VehicleType.CAR_VAN, 3500,  "truck"),
    (VehicleType.CAR_VAN, 5000,  "truck"),
    (VehicleType.CAR_VAN, 12000, "truck"),

    # < 3500 kg car/van → driving
    (VehicleType.CAR_VAN, None, "driving"),
    (VehicleType.CAR_VAN, 500,  "driving"),
    (VehicleType.CAR_VAN, 3499, "driving"),
])
def test_profile_by_type_and_weight(vehicle_type, gross_weight_kg, expected_profile):
    profile = resolve_osrm_profile_from_values(vehicle_type, gross_weight_kg=gross_weight_kg)
    assert profile == expected_profile, (
        f"{vehicle_type} @ {gross_weight_kg}kg → expected {expected_profile!r}, got {profile!r}"
    )


def test_explicit_spec_osrm_profile_overrides_all():
    """A VehicleSpec with osrm_profile set takes priority over type and weight."""
    spec = SimpleNamespace(osrm_profile="custom_heavy", gross_weight_kg=None)
    profile = resolve_osrm_profile_from_values(VehicleType.CAR_VAN, spec=spec)
    assert profile == "custom_heavy"


def test_spec_without_osrm_profile_falls_through_to_weight():
    """A VehicleSpec with osrm_profile=None still uses weight logic."""
    spec = SimpleNamespace(osrm_profile=None, gross_weight_kg=8000)
    profile = resolve_osrm_profile_from_values(VehicleType.CAR_VAN, spec=spec)
    assert profile == "truck"


def test_simple_namespace_mock_without_osrm_profile_attr():
    """SimpleNamespace missing osrm_profile entirely should not raise AttributeError."""
    spec = SimpleNamespace(gross_weight_kg=2000)  # no osrm_profile attribute
    profile = resolve_osrm_profile_from_values(VehicleType.CAR_VAN, spec=spec)
    assert profile == "driving"


def test_simple_namespace_mock_without_any_spec_attrs():
    """SimpleNamespace with no relevant attributes falls back cleanly."""
    spec = SimpleNamespace()
    profile = resolve_osrm_profile_from_values(VehicleType.TRUCK, spec=spec)
    assert profile == "truck"


def test_gross_weight_from_spec_used_when_kwarg_is_none():
    """When gross_weight_kg kwarg is None, spec.gross_weight_kg is used."""
    spec = SimpleNamespace(osrm_profile=None, gross_weight_kg=5000)
    profile = resolve_osrm_profile_from_values(VehicleType.CAR_VAN, spec=spec, gross_weight_kg=None)
    assert profile == "truck"


def test_kwarg_gross_weight_takes_precedence_over_spec():
    """Explicit gross_weight_kg kwarg is used before spec.gross_weight_kg."""
    spec = SimpleNamespace(osrm_profile=None, gross_weight_kg=500)   # spec says light
    profile = resolve_osrm_profile_from_values(VehicleType.CAR_VAN, spec=spec, gross_weight_kg=7000)
    assert profile == "truck"   # kwarg says heavy → truck


def test_osrm_profile_base_urls_covers_all_returned_profiles(monkeypatch):
    """Sanity check: all profiles returned by resolve_osrm_profile_from_values
    have a corresponding entry in OSRM_PROFILE_BASE_URLS (or fall back to
    OSRM_BASE_URL). This catches misconfigured compose env."""
    from app.algorithms.osrm_client import _profile_base_url
    from app.config import settings

    fake_urls = {
        "driving":    "http://osrm-car:5000",
        "motorcycle": "http://osrm-car:5000",
        "truck":      "http://osrm-truck:5000",
    }
    monkeypatch.setattr(settings, "OSRM_PROFILE_BASE_URLS", fake_urls)
    monkeypatch.setattr(settings, "OSRM_BASE_URL", "http://osrm-car:5000")

    for vehicle_type in VehicleType:
        for weight in (None, 1000, 3500, 12000):
            profile = resolve_osrm_profile_from_values(vehicle_type, gross_weight_kg=weight)
            url = _profile_base_url(profile)
            assert url, f"No base URL resolved for profile={profile!r}"
            if profile == "truck":
                assert url == "http://osrm-truck:5000", \
                    f"Truck profile must point to truck server, got {url!r}"
            else:
                assert url == "http://osrm-car:5000", \
                    f"Non-truck profile {profile!r} must point to car server, got {url!r}"
