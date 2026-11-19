"""Unit tests for geofencing / haversine distance calculation."""
import pytest

from app.utils.geo import haversine_meters, point_in_geojson_polygon


class TestHaversine:
    """Test haversine distance formula with known coordinate pairs."""

    def test_same_point_returns_zero(self):
        assert haversine_meters(40.7128, -74.0060, 40.7128, -74.0060) == pytest.approx(0.0, abs=0.1)

    def test_new_york_to_los_angeles(self):
        """NYC (40.7128, -74.0060) to LA (34.0522, -118.2437) ≈ 3,944 km."""
        distance = haversine_meters(40.7128, -74.0060, 34.0522, -118.2437)
        assert 3_930_000 < distance < 3_960_000

    def test_london_to_paris(self):
        """London (51.5074, -0.1278) to Paris (48.8566, 2.3522) ≈ 343 km."""
        distance = haversine_meters(51.5074, -0.1278, 48.8566, 2.3522)
        assert 340_000 < distance < 346_000

    def test_short_distance_within_city(self):
        """Two points ~1 km apart in Istanbul."""
        # Taksim (41.0370, 28.9850) to Galata Tower (41.0256, 28.9741)
        distance = haversine_meters(41.0370, 28.9850, 41.0256, 28.9741)
        assert 1_300 < distance < 1_600

    def test_within_arrival_radius(self):
        """Two points ~50m apart should be within 100m arrival radius."""
        # Slightly offset coordinates
        lat1, lon1 = 41.0370, 28.9850
        lat2, lon2 = 41.03705, 28.98506  # ~5m apart
        distance = haversine_meters(lat1, lon1, lat2, lon2)
        assert distance < 100  # Within arrival radius

    def test_antipodal_points(self):
        """Opposite sides of Earth ≈ 20,015 km (half circumference)."""
        distance = haversine_meters(0.0, 0.0, 0.0, 180.0)
        assert 20_000_000 < distance < 20_050_000

    def test_symmetry(self):
        """Distance A→B should equal distance B→A."""
        d1 = haversine_meters(40.7128, -74.0060, 34.0522, -118.2437)
        d2 = haversine_meters(34.0522, -118.2437, 40.7128, -74.0060)
        assert d1 == pytest.approx(d2, abs=0.01)

    def test_point_in_geojson_polygon(self):
        polygon = {
            "type": "Polygon",
            "coordinates": [[
                [28.90, 41.00],
                [29.10, 41.00],
                [29.10, 41.10],
                [28.90, 41.10],
                [28.90, 41.00],
            ]],
        }
        assert point_in_geojson_polygon(41.05, 29.00, polygon)
        assert not point_in_geojson_polygon(41.20, 29.20, polygon)
