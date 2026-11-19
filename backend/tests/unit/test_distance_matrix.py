"""Unit tests for the distance matrix / OSRM client module.

Tests cover cache key determinism, unreachable detection, and matrix validation.
Haversine fallback tests removed — the system now requires OSRM.
"""

from app.algorithms.osrm_client import (
    _LARGE_DISTANCE,
    MatrixResult,
    _has_unreachable_block,
    _matrix_cache_key,
)


class TestCacheKey:
    """Deterministic cache key generation."""

    def test_same_coords_same_key(self):
        c1 = [(41.00001, 29.00001), (41.01, 29.02)]
        c2 = [(41.00001, 29.00001), (41.01, 29.02)]
        assert _matrix_cache_key(c1) == _matrix_cache_key(c2)

    def test_different_coords_different_key(self):
        c1 = [(41.0, 29.0), (41.01, 29.02)]
        c2 = [(41.0, 29.0), (41.01, 29.03)]
        assert _matrix_cache_key(c1) != _matrix_cache_key(c2)

    def test_rounding_normalises(self):
        """Tiny float noise should be rounded away."""
        c1 = [(41.000001, 29.000001)]
        c2 = [(41.000002, 29.000002)]
        assert _matrix_cache_key(c1) == _matrix_cache_key(c2)

    def test_key_format(self):
        key = _matrix_cache_key([(41.0, 29.0)])
        # Cache keys now embed the OSRM profile so multi-profile fleets don't
        # collide on the same coords. Format: dm:{version}:{profile}:{hash16}.
        assert key.startswith("dm:v2:driving:")
        assert len(key) == len("dm:v2:driving:") + 16

    def test_key_includes_profile(self):
        car_key = _matrix_cache_key([(41.0, 29.0)], profile="driving")
        truck_key = _matrix_cache_key([(41.0, 29.0)], profile="truck")
        assert car_key != truck_key
        assert truck_key.startswith("dm:v2:truck:")


class TestUnreachableDetection:
    def test_detects_unreachable_row(self):
        matrix = [
            [0, _LARGE_DISTANCE, _LARGE_DISTANCE],
            [100, 0, 120],
            [140, 110, 0],
        ]
        assert _has_unreachable_block(matrix)

    def test_all_reachable_returns_false(self):
        matrix = [
            [0, 100, 200],
            [100, 0, 120],
            [200, 120, 0],
        ]
        assert not _has_unreachable_block(matrix)


class TestMatrixResult:
    """MatrixResult data class basics."""

    def test_source_field(self):
        result = MatrixResult(distances=[[0]], durations=[[0]], source="osrm")
        assert result.source == "osrm"

    def test_cache_source(self):
        result = MatrixResult(distances=[[0]], durations=[[0]], source="cache")
        assert result.source == "cache"
