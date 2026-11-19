"""Tests for constraint diagnostics."""

from app.algorithms.constraint_diagnostics import diagnose_unassigned
from app.algorithms.vrp_solver import OrderData, VehicleData, VRPInput
from app.models.enums import VehicleType


def _make_input(orders, vehicles, n_locs=10):
    locations = [(0.0, 0.0)] * n_locs
    duration_matrix = [[300] * n_locs for _ in range(n_locs)]
    distance_matrix = [[1000] * n_locs for _ in range(n_locs)]
    for i in range(n_locs):
        duration_matrix[i][i] = 0
        distance_matrix[i][i] = 0
    return VRPInput(
        locations=locations,
        distance_matrix=distance_matrix,
        duration_matrix=duration_matrix,
        orders=orders,
        vehicles=vehicles,
    )


def _order(order_id="o1", weight=1000, **kw):
    defaults = {
        "order_id": order_id,
        "pickup_index": 1,
        "delivery_index": 2,
        "weight_grams": weight,
        "earliest_time_s": 28800,
        "latest_time_s": 64800,
        "service_time_s": 300,
    }
    defaults.update(kw)
    return OrderData(**defaults)


def _vehicle(courier_id="c1", capacity=50000, **kw):
    defaults = {
        "courier_id": courier_id,
        "vehicle_type": VehicleType.CAR_VAN,
        "capacity_grams": capacity,
        "depot_index": 0,
        "start_time_s": 28800,
        "end_time_s": 64800,
    }
    defaults.update(kw)
    return VehicleData(**defaults)


class TestDiagnoseUnassigned:
    def test_no_unassigned_returns_empty(self):
        vrp = _make_input([_order()], [_vehicle()])
        result = diagnose_unassigned(vrp, [])
        assert result == []

    def test_capacity_blocking(self):
        order = _order(weight=60000)
        vehicle = _vehicle(capacity=50000)
        vrp = _make_input([order], [vehicle])
        result = diagnose_unassigned(vrp, ["o1"])
        assert len(result) == 1
        reasons = result[0].blocking_reasons
        cap_reasons = [r for r in reasons if r.constraint == "capacity_weight"]
        assert len(cap_reasons) == 1
        assert "60000g" in cap_reasons[0].message

    def test_fragile_motorcycle_blocking(self):
        order = _order(is_fragile=True)
        vehicle = _vehicle(vehicle_type=VehicleType.MOTORCYCLE)
        vrp = _make_input([order], [vehicle])
        result = diagnose_unassigned(vrp, ["o1"])
        assert len(result) == 1
        reasons = result[0].blocking_reasons
        fragile_reasons = [r for r in reasons if r.constraint == "fragile_restriction"]
        assert len(fragile_reasons) == 1

    def test_time_window_blocking(self):
        order = _order(earliest_time_s=60000, latest_time_s=61000)
        vehicle = _vehicle(start_time_s=28800, end_time_s=50000)
        vrp = _make_input([order], [vehicle])
        result = diagnose_unassigned(vrp, ["o1"])
        assert len(result) == 1
        assert len(result[0].blocking_reasons) > 0
        constraint_types = {r.constraint for r in result[0].blocking_reasons}
        assert constraint_types & {"time_window_delivery", "time_window_pickup", "shift_hours"}

    def test_suggestion_is_generated(self):
        order = _order(weight=60000)
        vehicle = _vehicle(capacity=50000)
        vrp = _make_input([order], [vehicle])
        result = diagnose_unassigned(vrp, ["o1"])
        assert result[0].suggestion
        assert "capacity" in result[0].suggestion.lower() or "weight" in result[0].suggestion.lower()

    def test_multiple_unassigned(self):
        orders = [_order("o1", weight=60000), _order("o2", weight=70000, pickup_index=3, delivery_index=4)]
        vehicle = _vehicle(capacity=50000)
        vrp = _make_input(orders, [vehicle])
        result = diagnose_unassigned(vrp, ["o1", "o2"])
        assert len(result) == 2

    def test_to_dict_format(self):
        order = _order(weight=60000)
        vehicle = _vehicle(capacity=50000)
        vrp = _make_input([order], [vehicle])
        result = diagnose_unassigned(vrp, ["o1"])
        d = result[0].to_dict()
        assert "order_id" in d
        assert "blocking_reasons" in d
        assert "suggestion" in d
        assert isinstance(d["blocking_reasons"], list)
