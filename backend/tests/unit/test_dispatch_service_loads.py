import uuid
from types import SimpleNamespace

from app.models.enums import StopStatus, StopType
from app.services import dispatch_service


def test_estimate_route_starting_load_counts_already_picked_orders_only():
    order_a = uuid.uuid4()
    order_b = uuid.uuid4()
    order_c = uuid.uuid4()

    stops = [
        # Order A: only delivery remains -> should count as onboard.
        SimpleNamespace(order_id=order_a, status=StopStatus.ARRIVED, stop_type=StopType.DELIVERY),
        # Order B: pickup and delivery both pending -> not yet onboard at route start.
        SimpleNamespace(order_id=order_b, status=StopStatus.PENDING, stop_type=StopType.PICKUP),
        SimpleNamespace(order_id=order_b, status=StopStatus.PENDING, stop_type=StopType.DELIVERY),
        # Order C: terminal stop ignored.
        SimpleNamespace(order_id=order_c, status=StopStatus.COMPLETED, stop_type=StopType.DELIVERY),
    ]
    weights = {
        order_a: 3_000,
        order_b: 5_000,
        order_c: 2_000,
    }

    starting_load = dispatch_service._estimate_route_starting_load_grams(stops, weights)
    assert starting_load == 3_000
