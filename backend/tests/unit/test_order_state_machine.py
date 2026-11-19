"""Unit tests for order state machine transitions."""
import pytest
from fastapi import HTTPException

from app.models.enums import ORDER_STATE_TRANSITIONS, OrderStatus
from app.services.order_service import validate_state_transition


class TestOrderStateMachine:
    """Test all valid and invalid state transitions for orders."""

    # Valid transitions
    def test_draft_to_pending(self):
        validate_state_transition(OrderStatus.DRAFT, OrderStatus.PENDING)

    def test_draft_to_cancelled(self):
        validate_state_transition(OrderStatus.DRAFT, OrderStatus.CANCELLED)

    def test_pending_to_assigned(self):
        validate_state_transition(OrderStatus.PENDING, OrderStatus.ASSIGNED)

    def test_pending_to_cancelled(self):
        validate_state_transition(OrderStatus.PENDING, OrderStatus.CANCELLED)

    def test_assigned_to_picked_up(self):
        validate_state_transition(OrderStatus.ASSIGNED, OrderStatus.PICKED_UP)

    def test_assigned_to_cancelled(self):
        validate_state_transition(OrderStatus.ASSIGNED, OrderStatus.CANCELLED)

    def test_assigned_to_pending(self):
        validate_state_transition(OrderStatus.ASSIGNED, OrderStatus.PENDING)

    def test_picked_up_to_in_transit(self):
        validate_state_transition(OrderStatus.PICKED_UP, OrderStatus.IN_TRANSIT)

    def test_in_transit_to_delivered(self):
        validate_state_transition(OrderStatus.IN_TRANSIT, OrderStatus.DELIVERED)

    def test_in_transit_to_failed(self):
        validate_state_transition(OrderStatus.IN_TRANSIT, OrderStatus.FAILED)

    def test_failed_to_rescheduled(self):
        validate_state_transition(OrderStatus.FAILED, OrderStatus.RESCHEDULED)

    def test_failed_to_cancelled(self):
        validate_state_transition(OrderStatus.FAILED, OrderStatus.CANCELLED)

    def test_rescheduled_to_pending(self):
        validate_state_transition(OrderStatus.RESCHEDULED, OrderStatus.PENDING)

    def test_pending_to_expired(self):
        validate_state_transition(OrderStatus.PENDING, OrderStatus.EXPIRED)

    def test_expired_to_rescheduled(self):
        validate_state_transition(OrderStatus.EXPIRED, OrderStatus.RESCHEDULED)

    # Invalid transitions
    def test_pending_to_delivered_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_state_transition(OrderStatus.PENDING, OrderStatus.DELIVERED)
        assert exc_info.value.status_code == 400

    def test_delivered_to_anything_raises(self):
        for target in OrderStatus:
            if target == OrderStatus.DELIVERED:
                continue
            with pytest.raises(HTTPException):
                validate_state_transition(OrderStatus.DELIVERED, target)

    def test_cancelled_to_anything_raises(self):
        for target in OrderStatus:
            if target == OrderStatus.CANCELLED:
                continue
            with pytest.raises(HTTPException):
                validate_state_transition(OrderStatus.CANCELLED, target)

    def test_expired_to_non_rescheduled_raises(self):
        for target in OrderStatus:
            if target == OrderStatus.RESCHEDULED:
                continue
            with pytest.raises(HTTPException):
                validate_state_transition(OrderStatus.EXPIRED, target)

    def test_assigned_to_delivered_raises(self):
        with pytest.raises(HTTPException):
            validate_state_transition(OrderStatus.ASSIGNED, OrderStatus.DELIVERED)

    def test_draft_to_assigned_raises(self):
        with pytest.raises(HTTPException):
            validate_state_transition(OrderStatus.DRAFT, OrderStatus.ASSIGNED)

    def test_picked_up_to_delivered_raises(self):
        """Must go through IN_TRANSIT first."""
        with pytest.raises(HTTPException):
            validate_state_transition(OrderStatus.PICKED_UP, OrderStatus.DELIVERED)

    def test_all_states_have_transition_rules(self):
        """Every OrderStatus value must be in the transition map."""
        for s in OrderStatus:
            assert s in ORDER_STATE_TRANSITIONS, f"Missing transition rules for {s.value}"
