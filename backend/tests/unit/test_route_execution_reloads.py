import uuid
from types import SimpleNamespace

from app.models.enums import StopType
from app.services.route_execution_service import _collect_trip_load_order_ids


def _stop(sequence_number: int, stop_type: StopType, order_id: uuid.UUID | None = None):
    return SimpleNamespace(
        sequence_number=sequence_number,
        stop_type=stop_type,
        order_id=order_id,
    )


def test_route_start_loads_hidden_depot_pickup_deliveries():
    first_order = uuid.uuid4()
    second_order = uuid.uuid4()
    later_order = uuid.uuid4()
    stops = [
        _stop(0, StopType.DELIVERY, first_order),
        _stop(1, StopType.PICKUP, second_order),
        _stop(2, StopType.DELIVERY, second_order),
        _stop(3, StopType.DEPOT_RETURN),
        _stop(4, StopType.DEPOT_RELOAD),
        _stop(5, StopType.DELIVERY, later_order),
    ]

    assert _collect_trip_load_order_ids(stops, after_sequence=None) == [
        first_order,
        second_order,
    ]


def test_depot_reload_loads_next_trip_until_next_depot_boundary():
    first_order = uuid.uuid4()
    reloaded_order = uuid.uuid4()
    duplicate_delivery = reloaded_order
    next_trip_order = uuid.uuid4()
    stops = [
        _stop(0, StopType.DELIVERY, first_order),
        _stop(1, StopType.DEPOT_RETURN),
        _stop(2, StopType.DEPOT_RELOAD),
        _stop(3, StopType.PICKUP, reloaded_order),
        _stop(4, StopType.DELIVERY, duplicate_delivery),
        _stop(5, StopType.DEPOT_RETURN),
        _stop(6, StopType.DEPOT_RELOAD),
        _stop(7, StopType.DELIVERY, next_trip_order),
    ]

    assert _collect_trip_load_order_ids(stops, after_sequence=2) == [reloaded_order]
