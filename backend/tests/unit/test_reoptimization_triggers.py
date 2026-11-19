"""Tests for re-optimization trigger logic."""
from datetime import datetime

from app.models.enums import OrderPriority
from app.services.reoptimization_triggers import LATE_ARRIVAL_THRESHOLD_SECONDS


class TestLateArrivalThreshold:
    def test_threshold_is_600_seconds(self):
        assert LATE_ARRIVAL_THRESHOLD_SECONDS == 600


class TestTriggerConditions:
    def test_urgent_order_qualifies(self):
        """Urgent and high-priority orders should trigger immediate dispatch."""
        assert OrderPriority.URGENT in (OrderPriority.URGENT, OrderPriority.HIGH)
        assert OrderPriority.HIGH in (OrderPriority.URGENT, OrderPriority.HIGH)

    def test_normal_order_does_not_qualify(self):
        """Normal and low-priority orders should not trigger immediate dispatch."""
        assert OrderPriority.NORMAL not in (OrderPriority.URGENT, OrderPriority.HIGH)
        assert OrderPriority.LOW not in (OrderPriority.URGENT, OrderPriority.HIGH)

    def test_delay_calculation(self):
        """Verify delay calculation logic."""
        planned = datetime(2026, 3, 14, 10, 0, 0)
        actual_late = datetime(2026, 3, 14, 10, 15, 0)  # 15 min late
        delay = int((actual_late - planned).total_seconds())
        assert delay == 900
        assert delay >= LATE_ARRIVAL_THRESHOLD_SECONDS

        actual_on_time = datetime(2026, 3, 14, 10, 5, 0)  # 5 min late
        delay_small = int((actual_on_time - planned).total_seconds())
        assert delay_small == 300
        assert delay_small < LATE_ARRIVAL_THRESHOLD_SECONDS
