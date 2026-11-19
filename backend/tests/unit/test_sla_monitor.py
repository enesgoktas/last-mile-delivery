"""Tests for the SLA monitor service — unit-level logic tests."""

from app.services.sla_monitor_service import _build_dedupe_key


class TestDedupeKey:
    def test_deterministic(self):
        key1 = _build_dedupe_key("route-1", "stop-1")
        key2 = _build_dedupe_key("route-1", "stop-1")
        assert key1 == key2

    def test_different_inputs_different_keys(self):
        key1 = _build_dedupe_key("route-1", "stop-1")
        key2 = _build_dedupe_key("route-1", "stop-2")
        assert key1 != key2

    def test_key_length(self):
        key = _build_dedupe_key("route-1", "stop-1")
        assert len(key) == 32

    def test_key_is_hex(self):
        key = _build_dedupe_key("route-1", "stop-1")
        int(key, 16)  # Should not raise
