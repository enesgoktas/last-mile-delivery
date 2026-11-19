from app.models.enums import OrderType
from app.schemas.operations_policy import OperationsPolicy, ProofOfDeliveryRequirements
from app.services.route_service import get_pod_policy, resolve_stop_wait_time_minutes


def test_default_policy_uses_optional_signature():
    policy = get_pod_policy(OrderType.SCHEDULED)
    assert policy["photo_required"] is True
    assert policy["signature_required"] is False
    assert policy["otp_required"] is False


def test_order_type_override_is_merged_with_defaults():
    operations_policy = OperationsPolicy()
    operations_policy.proof_of_delivery.by_order_type[OrderType.REVERSE] = (
        ProofOfDeliveryRequirements(
            require_photo=True,
            photo_count=3,
            require_signature=True,
            require_otp=True,
            receiver_name_mode="REQUIRED",
            notes_mode="REQUIRED",
        )
    )

    policy = get_pod_policy(OrderType.REVERSE, operations_policy)

    assert policy["photo_required"] is True
    assert policy["photo_count"] == 3
    assert policy["signature_required"] is True
    assert policy["otp_required"] is True
    assert policy["receiver_name_mode"] == "REQUIRED"


def test_on_demand_order_uses_defaults_when_no_override():
    """ON_DEMAND orders fall back to the default POD policy when not overridden."""
    policy = get_pod_policy(OrderType.ON_DEMAND)
    # Default policy: require_photo=True, photo_count=1, no signature/otp required
    assert policy["photo_required"] is True
    assert policy["photo_count"] == 1
    assert policy["signature_required"] is False
    assert policy["otp_required"] is False


def test_bulk_order_uses_defaults_when_no_override():
    """BULK orders fall back to the default POD policy when not overridden."""
    policy = get_pod_policy(OrderType.BULK)
    assert policy["photo_required"] is True
    assert policy["photo_count"] == 1


def test_same_day_order_type_override():
    """A custom SCHEDULED override is applied for same-day-style workflows."""
    operations_policy = OperationsPolicy()
    operations_policy.proof_of_delivery.by_order_type[OrderType.SCHEDULED] = (
        ProofOfDeliveryRequirements(
            require_photo=True,
            photo_count=2,
            require_signature=True,
            require_otp=False,
            receiver_name_mode="REQUIRED",
            notes_mode="OPTIONAL",
        )
    )

    policy = get_pod_policy(OrderType.SCHEDULED, operations_policy)

    assert policy["photo_count"] == 2
    assert policy["signature_required"] is True
    assert policy["receiver_name_mode"] == "REQUIRED"
    assert policy["notes_mode"] == "OPTIONAL"


def test_notes_mode_optional_by_default():
    """Default notes_mode is OPTIONAL."""
    policy = get_pod_policy(OrderType.SCHEDULED)
    assert policy["notes_mode"] == "OPTIONAL"


def test_notes_mode_off_override():
    """Override can set notes_mode to OFF to disable the field entirely."""
    operations_policy = OperationsPolicy()
    operations_policy.proof_of_delivery.by_order_type[OrderType.ON_DEMAND] = (
        ProofOfDeliveryRequirements(notes_mode="OFF")
    )
    policy = get_pod_policy(OrderType.ON_DEMAND, operations_policy)
    assert policy["notes_mode"] == "OFF"


def test_photo_count_defaults_to_1():
    """Default photo_count is 1 for all order types when no override exists."""
    for order_type in OrderType:
        policy = get_pod_policy(order_type)
        assert policy["photo_count"] == 1, f"Expected photo_count=1 for {order_type}"


def test_none_policy_falls_back_to_defaults():
    """Passing None explicitly triggers the default policy."""
    policy = get_pod_policy(OrderType.REVERSE, None)
    assert policy["photo_required"] is True
    assert policy["signature_required"] is False


def test_override_does_not_affect_other_order_types():
    """An override for REVERSE does not change the policy for SCHEDULED."""
    operations_policy = OperationsPolicy()
    operations_policy.proof_of_delivery.by_order_type[OrderType.REVERSE] = (
        ProofOfDeliveryRequirements(
            require_signature=True,
            require_otp=True,
        )
    )

    reverse_policy = get_pod_policy(OrderType.REVERSE, operations_policy)
    scheduled_policy = get_pod_policy(OrderType.SCHEDULED, operations_policy)

    assert reverse_policy["signature_required"] is True
    # SCHEDULED has no override, falls back to default (no signature)
    assert scheduled_policy["signature_required"] is False


def test_receiver_name_mode_defaults_to_optional():
    """Default receiver_name_mode is OPTIONAL."""
    policy = get_pod_policy(OrderType.SCHEDULED)
    assert policy["receiver_name_mode"] == "OPTIONAL"


def test_wait_time_uses_base_value_when_pod_is_not_high_risk():
    pod_policy = {
        "signature_required": False,
        "otp_required": False,
        "receiver_name_mode": "OPTIONAL",
    }
    assert resolve_stop_wait_time_minutes(base_wait_time_minutes=3, pod_policy=pod_policy) == 3


def test_wait_time_enforces_minimum_for_high_risk_pod():
    pod_policy = {
        "signature_required": True,
        "otp_required": False,
        "receiver_name_mode": "REQUIRED",
    }
    assert resolve_stop_wait_time_minutes(base_wait_time_minutes=3, pod_policy=pod_policy) == 8
    assert resolve_stop_wait_time_minutes(base_wait_time_minutes=12, pod_policy=pod_policy) == 12
