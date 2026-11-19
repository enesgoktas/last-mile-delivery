"""Unit tests for M6 decomposition — clustering, portals, sparse graph, pipeline,
rolling horizon, and border repair."""
from __future__ import annotations

import copy
import random

import pytest

from app.algorithms.rich_vrp.bench.datasets import _build_synthetic, load_synthetic
from app.algorithms.rich_vrp.constructive.greedy_seed import seed_greedy
from app.algorithms.rich_vrp.decomposition.clustering import (
    ClusterConfig,
    DECOMP_THRESHOLD,
    ZoneCluster,
    cluster_problem,
)
from app.algorithms.rich_vrp.decomposition.pipeline import DecompResult, decompose_and_solve
from app.algorithms.rich_vrp.decomposition.portals import find_portals
from app.algorithms.rich_vrp.decomposition.rolling_horizon import (
    solve_batch,
    solve_reactive,
    solve_tactical,
)
from app.algorithms.rich_vrp.decomposition.sparse_graph import build_sparse_graph
from app.algorithms.rich_vrp.domain.builders import build_problem
from app.algorithms.rich_vrp.domain.models import VisitKind
from app.algorithms.rich_vrp.matrices.provider import build_zone_problem, extract_zone_nodes
from app.algorithms.rich_vrp.recombination.border_repair import border_repair


@pytest.fixture(scope="module")
def small_problem_and_solution():
    vrp = load_synthetic("synthetic_30")
    problem = build_problem(vrp)
    solution = seed_greedy(problem, candidate_size=12)
    return problem, solution


# ── Clustering ───────────────────────────────────────────────────────────────

def test_cluster_small_problem_single_zone(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    assert len(clusters) == 1
    assert set(clusters[0].request_ids) == {r.id for r in problem.requests}


def test_cluster_returns_zone_clusters(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    assert all(isinstance(c, ZoneCluster) for c in clusters)


def test_cluster_covers_all_requests(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    covered = {rid for c in clusters for rid in c.request_ids}
    assert covered == {r.id for r in problem.requests}


def test_cluster_no_duplicate_requests(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    all_rids = [rid for c in clusters for rid in c.request_ids]
    assert len(all_rids) == len(set(all_rids))


def test_cluster_vehicles_assigned(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    # At least the single zone should have all vehicles
    total_vehicles = sum(len(c.vehicle_indices) for c in clusters)
    assert total_vehicles == len(problem.vehicles)


def test_cluster_custom_config(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    config = ClusterConfig(max_zone_size=10, min_zone_size=2)
    clusters = cluster_problem(problem, config)
    covered = {rid for c in clusters for rid in c.request_ids}
    assert covered == {r.id for r in problem.requests}


def test_cluster_distributes_vehicles_proportionally_with_shared_depot():
    """Regression: when all vehicles share one depot, every zone must still
    get vehicles in rough proportion to its demand. Pure-proximity assignment
    used to dump every vehicle into the zone closest to the depot, leaving
    other zones with at most one vehicle each — which dropped ~40% of orders
    on a 600-order workload. See clustering._assign_vehicles."""
    vrp = _build_synthetic(n_orders=600, seed=42)  # 60 vehicles, all at depot 0
    problem = build_problem(vrp)
    clusters = cluster_problem(problem, ClusterConfig(max_zone_size=80))

    assert len(clusters) >= 5  # 600 orders → multiple zones expected
    # Every non-empty zone gets at least one vehicle
    for c in clusters:
        if c.request_ids:
            assert len(c.vehicle_indices) >= 1, f"zone {c.cluster_id} has no vehicles"

    # No single zone holds >50% of the fleet (was ~70% with the old code)
    n_vehicles = len(problem.vehicles)
    max_share = max(len(c.vehicle_indices) for c in clusters)
    assert max_share / n_vehicles < 0.5, (
        f"one zone holds {max_share}/{n_vehicles} vehicles — "
        "vehicle assignment is not demand-proportional"
    )

    # Total vehicles assigned = fleet size (no leaks)
    total_assigned = sum(len(c.vehicle_indices) for c in clusters)
    assert total_assigned == n_vehicles

    # Every vehicle assigned to exactly one zone
    seen: set[int] = set()
    for c in clusters:
        for v_idx in c.vehicle_indices:
            assert v_idx not in seen
            seen.add(v_idx)
    assert len(seen) == n_vehicles


# ── Portals ──────────────────────────────────────────────────────────────────

def test_portals_single_zone_empty(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    portals = find_portals(clusters, problem, k=3)
    # Single zone has no portals needed
    assert all(len(v) == 0 for v in portals.values())


def test_portals_returns_node_indices(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    # Force two clusters by using a small max_zone_size
    config = ClusterConfig(max_zone_size=8, min_zone_size=2)
    clusters = cluster_problem(problem, config)
    portals = find_portals(clusters, problem, k=2)
    for cid, nodes in portals.items():
        for node in nodes:
            assert isinstance(node, int)
            assert 0 <= node < len(problem.locations)


# ── Sparse graph ──────────────────────────────────────────────────────────────

def test_sparse_graph_builds(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    graph = build_sparse_graph(problem, clusters, k=4)
    assert graph.k == 4


def test_sparse_graph_neighbours_bounded(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    graph = build_sparse_graph(problem, clusters, k=4)
    for node, nbrs in graph.neighbours.items():
        assert len(nbrs) <= 4


def test_sparse_graph_cross_zone_estimate(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    config = ClusterConfig(max_zone_size=8, min_zone_size=2)
    clusters = cluster_problem(problem, config)
    graph = build_sparse_graph(problem, clusters, k=4)
    if len(clusters) >= 2:
        z0, z1 = clusters[0].cluster_id, clusters[1].cluster_id
        if clusters[0].request_ids and clusters[1].request_ids:
            n0 = problem.request(clusters[0].request_ids[0]).delivery_node
            n1 = problem.request(clusters[1].request_ids[0]).delivery_node
            cost = graph.estimate_cross_zone_duration(n0, n1, z0, z1, problem)
            assert cost >= 0


# ── Zone sub-problem ─────────────────────────────────────────────────────────

def test_build_zone_problem_subset(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    zone_p = build_zone_problem(problem, clusters[0])
    assert len(zone_p.requests) == len(clusters[0].request_ids)
    assert zone_p.matrix is problem.matrix


def test_extract_zone_nodes_nonempty(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    clusters = cluster_problem(problem)
    nodes = extract_zone_nodes(problem, clusters[0])
    assert len(nodes) > 0


# ── Pipeline ──────────────────────────────────────────────────────────────────

def test_pipeline_small_problem_direct_solve(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    rng = random.Random(0)
    result = decompose_and_solve(problem, time_budget_s=3.0, rng=rng)
    assert isinstance(result, DecompResult)
    assert isinstance(result.solution.routes, list)
    assert isinstance(result.solution.unserved, set)


def test_pipeline_no_duplicate_deliveries(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    rng = random.Random(1)
    result = decompose_and_solve(problem, time_budget_s=3.0, rng=rng)
    dids = [
        v.request_id
        for r in result.solution.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(dids) == len(set(dids))


def test_pipeline_covers_all_requests(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    rng = random.Random(2)
    result = decompose_and_solve(problem, time_budget_s=3.0, rng=rng)
    served = {
        v.request_id
        for r in result.solution.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    assert len(served) + len(result.solution.unserved) == len(problem.requests)


def test_pipeline_small_problem_metrics_populated(small_problem_and_solution):
    """DecompResult.metrics must always be populated with engine telemetry."""
    problem, _ = small_problem_and_solution
    rng = random.Random(3)
    result = decompose_and_solve(problem, time_budget_s=3.0, rng=rng)
    m = result.metrics
    assert isinstance(m, dict), "metrics must be a dict"
    assert m.get("total_iterations", 0) > 0, "total_iterations must be non-zero"
    assert "feasibility_ratio" in m
    assert "decomposed" in m


def test_pipeline_large_problem_decomposed_metrics(monkeypatch):
    """For problems above DECOMP_THRESHOLD, metrics must include zone telemetry."""
    from app.algorithms.rich_vrp.bench.datasets import _build_synthetic
    from app.algorithms.rich_vrp.domain.builders import build_problem
    from app.algorithms.rich_vrp.decomposition.clustering import ClusterConfig

    vrp = _build_synthetic(n_orders=250, seed=7)
    problem = build_problem(vrp)
    assert len(problem.requests) > DECOMP_THRESHOLD
    rng = random.Random(7)
    result = decompose_and_solve(
        problem, time_budget_s=5.0, rng=rng,
        cluster_config=ClusterConfig(max_zone_size=60),
    )
    m = result.metrics
    assert m.get("decomposed") is True, "large problem must set decomposed=True"
    assert m.get("zone_count", 0) >= 2, "must have decomposed into at least 2 zones"
    assert m.get("total_iterations", 0) > 0, "aggregated iterations must be non-zero"
    assert "zone_overtime_flags" in m
    assert isinstance(m["zone_overtime_flags"], list)
    assert len(m["zone_overtime_flags"]) == m["zone_count"]


# ── Border repair ─────────────────────────────────────────────────────────────

def test_border_repair_single_zone_unchanged(small_problem_and_solution):
    problem, solution = small_problem_and_solution
    clusters = cluster_problem(problem)
    result = border_repair(solution, problem, clusters=clusters)
    # Single zone — no cross-zone moves possible
    assert result is solution or result.objective <= solution.objective


def test_border_repair_no_worse(small_problem_and_solution):
    from app.algorithms.rich_vrp.objective.lexicographic import objective_for_solution
    problem, solution = small_problem_and_solution
    config = ClusterConfig(max_zone_size=8, min_zone_size=2)
    clusters = cluster_problem(problem, config)
    result = border_repair(solution, problem, clusters=clusters)
    old_obj = solution.objective or objective_for_solution(solution.routes, solution.unserved, problem)
    new_obj = result.objective or objective_for_solution(result.routes, result.unserved, problem)
    assert new_obj <= old_obj


def test_border_repair_no_duplicates(small_problem_and_solution):
    problem, solution = small_problem_and_solution
    clusters = cluster_problem(problem, ClusterConfig(max_zone_size=8, min_zone_size=2))
    result = border_repair(solution, problem, clusters=clusters)
    dids = [
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(dids) == len(set(dids))


# ── Rolling horizon ───────────────────────────────────────────────────────────

def test_rolling_horizon_batch(small_problem_and_solution):
    problem, _ = small_problem_and_solution
    rng = random.Random(10)
    result = solve_batch(problem, time_budget_s=3.0, rng=rng)
    assert isinstance(result.routes, list)


def test_rolling_horizon_tactical_no_locked(small_problem_and_solution):
    problem, solution = small_problem_and_solution
    rng = random.Random(11)
    result = solve_tactical(problem, solution, locked_vehicle_ids=set(), time_budget_s=3.0, rng=rng)
    assert isinstance(result.routes, list)
    served = {
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    assert len(served) + len(result.unserved) == len(problem.requests)


def test_rolling_horizon_tactical_locked_preserved(small_problem_and_solution):
    problem, solution = small_problem_and_solution
    rng = random.Random(12)
    if not solution.routes:
        pytest.skip("empty solution")
    locked_vid = solution.routes[0].vehicle.physical_id
    locked_rids = {
        v.request_id
        for v in solution.routes[0].visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    result = solve_tactical(
        problem, solution, locked_vehicle_ids={locked_vid}, time_budget_s=3.0, rng=rng
    )
    # All locked requests must still appear in the result
    served = {
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    for rid in locked_rids:
        assert rid in served or rid in result.unserved


def test_rolling_horizon_reactive_removal(small_problem_and_solution):
    problem, solution = small_problem_and_solution
    rng = random.Random(13)
    served_before = {
        v.request_id
        for r in solution.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    if not served_before:
        pytest.skip("no served requests")
    rid_to_remove = next(iter(served_before))
    result = solve_reactive(problem, solution, added_request_ids=set(), removed_request_ids={rid_to_remove}, rng=rng)
    served_after = {
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    }
    assert rid_to_remove not in served_after


def test_rolling_horizon_reactive_no_duplicates(small_problem_and_solution):
    problem, solution = small_problem_and_solution
    rng = random.Random(14)
    result = solve_reactive(problem, solution, added_request_ids=set(), removed_request_ids=set(), rng=rng)
    dids = [
        v.request_id
        for r in result.routes
        for v in r.visits
        if v.kind == VisitKind.DELIVERY and v.request_id
    ]
    assert len(dids) == len(set(dids))
