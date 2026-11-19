from app.services.eta_service import sum_path_duration_seconds


def test_sum_path_duration_seconds_empty():
    assert sum_path_duration_seconds([]) == 0


def test_sum_path_duration_seconds_sequential_edges():
    durations = [
        [0, 10, 20, 30],
        [10, 0, 15, 25],
        [20, 15, 0, 12],
        [30, 25, 12, 0],
    ]
    # Path: 0->1->2->3 => 10 + 15 + 12
    assert sum_path_duration_seconds(durations) == 37
