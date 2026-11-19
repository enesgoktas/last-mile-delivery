from types import SimpleNamespace

from app.services import dispatch_service


def _expected_positions(stop_count: int, pickup_position: int, delivery_position: int) -> dict[str, int]:
    route = [f"S{i}" for i in range(stop_count)]
    route.insert(pickup_position, "P")
    route.insert(delivery_position, "D")
    return {label: index for index, label in enumerate(route)}


def test_shift_route_stop_sequences_matches_final_position_contract():
    stop_count = 3
    for pickup_position in range(stop_count + 1):
        for delivery_position in range(pickup_position + 1, stop_count + 2):
            stops = [
                SimpleNamespace(sequence_number=index, label=f"S{index}")
                for index in range(stop_count)
            ]

            dispatch_service._shift_route_stop_sequences(
                stops,
                pickup_position=pickup_position,
                delivery_position=delivery_position,
            )

            expected = _expected_positions(
                stop_count=stop_count,
                pickup_position=pickup_position,
                delivery_position=delivery_position,
            )

            final_sequences = [stop.sequence_number for stop in stops]
            final_sequences.extend([pickup_position, delivery_position])

            assert sorted(final_sequences) == list(range(stop_count + 2))
            for stop in stops:
                assert stop.sequence_number == expected[stop.label]
