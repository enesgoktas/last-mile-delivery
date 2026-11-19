from __future__ import annotations

import pytest

from app.algorithms.adapters.human_dispatcher import HumanDispatcherBaselineAdapter
from app.algorithms.adapters.ortools import ORToolsCVRPTWAdapter
from app.algorithms.adapters.rich_alns import RichALNSAdapter
from app.algorithms.solver_registry import get_solver, known_solver_kinds


def test_all_three_kinds_resolve():
    assert isinstance(get_solver("human_dispatcher"), HumanDispatcherBaselineAdapter)
    assert isinstance(get_solver("ortools_cvrptw"), ORToolsCVRPTWAdapter)
    assert isinstance(get_solver("rich_alns"), RichALNSAdapter)


def test_adapter_names_match_registry_keys():
    for kind in ("human_dispatcher", "ortools_cvrptw", "rich_alns"):
        assert get_solver(kind).name == kind


def test_unknown_kind_raises_key_error():
    with pytest.raises(KeyError, match="unknown_solver"):
        get_solver("unknown_solver")


def test_known_solver_kinds_contains_all_three():
    kinds = known_solver_kinds()
    assert "human_dispatcher" in kinds
    assert "ortools_cvrptw" in kinds
    assert "rich_alns" in kinds


def test_registry_returns_singleton():
    assert get_solver("rich_alns") is get_solver("rich_alns")
    assert get_solver("ortools_cvrptw") is get_solver("ortools_cvrptw")
    assert get_solver("human_dispatcher") is get_solver("human_dispatcher")
