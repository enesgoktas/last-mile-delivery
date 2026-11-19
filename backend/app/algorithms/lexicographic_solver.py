"""Lexicographic batch solver with flexible depot reloads."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace

from app.algorithms.feasibility_diagnostics import (
    OrderFeasibility,
    diagnose_global_feasibility,
    summarize_cluster_feasibility,
    summarize_global_feasibility,
)
from app.algorithms.route_plan import (
    RoutePlan,
    RoutePlanStopSpec,
    build_depot_reload_specs,
    simulate_route_plan,
    validation_from_route_plans,
)
from app.algorithms.solution_validator import ValidationResult, Violation, ViolationType
from app.algorithms.vrp_solver import (
    OrderData,
    SolverStatus,
    VehicleData,
    VRPInput,
    VRPSolution,
    build_solution_cost_breakdown,
    solve,
)
from app.models.enums import OrderPriority

_COST_SCALE = 60
_BOUNDED_INSERTION_POSITIONS = 12
_BLOCK_SEED_ROUTE_CANDIDATES = 12
_BLOCK_SEED_EXISTING_BLOCK_CANDIDATES = 4
_ENABLE_SPATIAL_SWEEP_SEED = False
_LARGE_BATCH_ORDER_COUNT = 300


@dataclass(frozen=True, slots=True)
class LexicographicSolveResult:
    solution: VRPSolution
    diagnostics: dict
    validation: ValidationResult


def lexicographic_solve_batch(
    vrp_input: VRPInput,
    *,
    time_limit_seconds: int | None = None,
    reload_time_s: int = 900,
    max_reload_count: int | None = None,
    spread_fleet: bool = False,
) -> LexicographicSolveResult:
    """Solve a physical-fleet VRP using assignment-first comparison."""
    started = time.monotonic()
    feasibility = diagnose_global_feasibility(vrp_input)
    feasible_order_ids = {d.order_id for d in feasibility if d.globally_feasible}
    trace: list[dict] = []

    seed_solution, seed_plans, seed_summary = _build_seed_solution(
        vrp_input,
        feasibility,
        reload_time_s=reload_time_s,
        max_reload_count=max_reload_count,
    )
    seed_validation = _validation_with_reload_limit(
        seed_plans,
        max_reload_count=max_reload_count,
    )
    incumbent = seed_solution
    incumbent_plans = seed_plans
    incumbent_validation = seed_validation
    incumbent_key = _solution_key(incumbent, incumbent_validation, feasible_order_ids)
    trace.append({"stage": "seed", "key": incumbent_key, **seed_summary})

    ortools_summary = {"attempted": False}
    budget = max(time_limit_seconds or 1, 1)
    if vrp_input.orders and vrp_input.vehicles and budget > 0:
        ortools_summary["attempted"] = True
        ortools_input = _with_lexicographic_unserved_penalties(vrp_input)
        initial_routes = _initial_routes_from_plans(vrp_input, seed_plans)
        raw_solution = solve(
            ortools_input,
            initial_routes=initial_routes,
            time_limit_seconds=budget,
            spread_fleet=spread_fleet,
        )
        normalized_solution, normalized_plans = _normalize_reload_solution(
            vrp_input,
            raw_solution,
            reload_time_s=reload_time_s,
        )
        normalized_validation = _validation_with_reload_limit(
            normalized_plans,
            max_reload_count=max_reload_count,
        )
        candidate_key = _solution_key(
            normalized_solution,
            normalized_validation,
            feasible_order_ids,
        )
        ortools_summary.update(
            {
                "solver_status": raw_solution.solver_status.value,
                "raw_unassigned_count": len(raw_solution.unassigned_orders),
                "normalized_unassigned_count": len(normalized_solution.unassigned_orders),
                "validation_error_count": normalized_validation.error_count,
                "candidate_key": candidate_key,
            }
        )
        trace.append({"stage": "ortools", "key": candidate_key, **ortools_summary})
        if _candidate_improves(
            candidate=normalized_solution,
            candidate_validation=normalized_validation,
            incumbent=incumbent,
            incumbent_validation=incumbent_validation,
            feasible_order_ids=feasible_order_ids,
            ):
            incumbent = normalized_solution
            incumbent_plans = normalized_plans
            incumbent_validation = normalized_validation
            incumbent_key = candidate_key

    repaired_solution, repaired_plans, repaired_validation, repair_summary = _repair_unassigned_feasible(
        vrp_input,
        incumbent,
        incumbent_plans,
        feasibility,
        reload_time_s=reload_time_s,
        max_reload_count=max_reload_count,
    )
    repaired_key = _solution_key(repaired_solution, repaired_validation, feasible_order_ids)
    trace.append({"stage": "repair_unassigned_feasible", "key": repaired_key, **repair_summary})
    if _candidate_improves(
        candidate=repaired_solution,
        candidate_validation=repaired_validation,
        incumbent=incumbent,
        incumbent_validation=incumbent_validation,
        feasible_order_ids=feasible_order_ids,
    ):
        incumbent = repaired_solution
        incumbent_plans = repaired_plans
        incumbent_validation = repaired_validation
        incumbent_key = repaired_key
    diagnostics = {
        "global_feasibility_summary": summarize_global_feasibility(feasibility),
        "cluster_feasibility_summary": summarize_cluster_feasibility(vrp_input, feasibility),
        "seed_solution_summary": seed_summary,
        "repair_summary": repair_summary,
        "optimization_trace": trace,
        "stage_assignment_counts": _stage_assignment_counts(
            vrp_input=vrp_input,
            feasibility=feasibility,
            seed_solution=seed_solution,
            ortools_summary=ortools_summary,
            repaired_solution=repaired_solution,
            final_solution=incumbent,
        ),
        "reload_summary": _reload_summary(incumbent),
        "unassigned_classification_summary": _classify_unassigned(incumbent, feasibility),
        "unassigned_order_diagnostics": _unassigned_order_diagnostics(
            incumbent,
            feasibility,
            seed_summary.get("rejection_details", {}),
            repair_summary.get("rejection_details", {}),
        ),
        "ortools_summary": ortools_summary,
        "lexicographic_key": incumbent_key,
        "assignment_count_status": (
            "all_globally_feasible_assigned"
            if not (set(incumbent.unassigned_orders) & feasible_order_ids)
            else "best_known_within_budget"
        ),
        "lexicographic_solver_runtime_ms": round((time.monotonic() - started) * 1000, 2),
    }
    return LexicographicSolveResult(
        solution=incumbent,
        diagnostics=diagnostics,
        validation=incumbent_validation,
    )


def _build_seed_solution(
    vrp_input: VRPInput,
    feasibility: list[OrderFeasibility],
    *,
    reload_time_s: int,
    max_reload_count: int | None,
) -> tuple[VRPSolution, list[RoutePlan], dict]:
    diag_by_order = {diag.order_id: diag for diag in feasibility}
    orders_by_id = {order.order_id: order for order in vrp_input.orders}
    plans = [
        simulate_route_plan(
            vrp_input,
            vehicle,
            [],
            reload_time_s=reload_time_s,
            orders_by_id=orders_by_id,
        )
        for vehicle in vrp_input.vehicles
    ]
    seed_candidates: list[tuple[str, list[RoutePlan], set[str], dict[str, dict]]] = []
    seed_candidates.append(("greedy_block_insertion", *_build_depot_block_seed_plans(
        vrp_input,
        plans,
        diag_by_order,
        reload_time_s=reload_time_s,
        max_reload_count=max_reload_count,
    )))
    if _ENABLE_SPATIAL_SWEEP_SEED:
        seed_candidates.append(("spatial_sweep", *_build_spatial_sweep_depot_seed_plans(
            vrp_input,
            plans,
            diag_by_order,
            reload_time_s=reload_time_s,
        )))

    feasible_order_ids = {
        diag.order_id
        for diag in feasibility
        if diag.globally_feasible
    }
    raw_candidate_summaries = {
        seed_strategy: {
            "depot_seed_assigned_count": len(assigned_ids),
            "raw_key": _seed_plan_score(candidate_plans, assigned_ids),
        }
        for seed_strategy, candidate_plans, assigned_ids, _ in seed_candidates
    }
    seed_candidates = _seed_candidates_to_finish(seed_candidates)

    best: tuple[tuple, VRPSolution, list[RoutePlan], dict] | None = None
    finished_candidate_summaries: dict[str, dict] = {}
    for seed_strategy, candidate_plans, assigned_ids, rejection_details in seed_candidates:
        solution, finished_plans, summary = _finish_seed_candidate(
            vrp_input,
            diag_by_order,
            candidate_plans,
            assigned_ids,
            rejection_details,
            seed_strategy=seed_strategy,
            reload_time_s=reload_time_s,
            max_reload_count=max_reload_count,
        )
        validation = _validation_with_reload_limit(
            finished_plans,
            max_reload_count=max_reload_count,
        )
        key = _solution_key(solution, validation, feasible_order_ids)
        finished_candidate_summaries[seed_strategy] = {
            "assigned_count": summary["assigned_count"],
            "unassigned_count": summary["unassigned_count"],
            "depot_seed_assigned_count": summary["depot_seed_assigned_count"],
            "validation_error_count": validation.error_count,
            "key": key,
        }
        if best is None or key < best[0]:
            best = (key, solution, finished_plans, summary)

    assert best is not None
    _, solution, plans, summary = best
    summary["depot_seed_candidates"] = raw_candidate_summaries
    summary["finished_seed_candidates"] = finished_candidate_summaries
    return solution, plans, summary


def _finish_seed_candidate(
    vrp_input: VRPInput,
    diag_by_order: dict[str, OrderFeasibility],
    plans: list[RoutePlan],
    block_assigned_order_ids: set[str],
    block_rejection_details: dict[str, dict],
    *,
    seed_strategy: str,
    reload_time_s: int,
    max_reload_count: int | None,
) -> tuple[VRPSolution, list[RoutePlan], dict]:
    plans = list(plans)
    orders_by_id = {order.order_id: order for order in vrp_input.orders}
    unassigned: list[str] = []
    rejection_reasons: dict[str, int] = {}
    rejection_details: dict[str, dict] = {}

    for order in sorted(vrp_input.orders, key=lambda o: _order_priority_key(o, diag_by_order)):
        if order.order_id in block_assigned_order_ids:
            continue
        diag = diag_by_order.get(order.order_id)
        if diag is not None and not diag.globally_feasible:
            unassigned.append(order.order_id)
            for reason in diag.blocking_reasons or ["globally_infeasible"]:
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            continue
        if (
            len(vrp_input.orders) >= _LARGE_BATCH_ORDER_COUNT
            and order.pickup_is_depot
            and order.order_id in block_rejection_details
        ):
            unassigned.append(order.order_id)
            block_detail = block_rejection_details.get(order.order_id, {})
            reason = block_detail.get("primary_reason", "route_interaction_or_solver_budget")
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            rejection_details[order.order_id] = {
                "stage": "seed",
                "primary_reason": reason,
                "candidate_rejection_reasons": block_detail.get("candidate_rejection_reasons", []),
            }
            continue

        best: tuple[tuple, int, RoutePlan] | None = None
        eligible_vehicle_ids = set(diag.eligible_vehicle_ids if diag else [])
        rejected_reasons: list[str] = []
        for route_idx, plan in enumerate(plans):
            if eligible_vehicle_ids and plan.vehicle.courier_id not in eligible_vehicle_ids:
                continue
            for specs in _candidate_specs_for_order(
                plan,
                order,
                max_reload_count=max_reload_count,
            ):
                candidate = simulate_route_plan(
                    vrp_input,
                    plan.vehicle,
                    specs,
                    reload_time_s=reload_time_s,
                    orders_by_id=orders_by_id,
                )
                if not candidate.is_valid:
                    rejected_reasons.extend(_route_plan_rejection_reasons(candidate))
                    continue
                score = _insertion_score(plan, candidate, plans)
                if best is None or score < best[0]:
                    best = (score, route_idx, candidate)
        if best is None:
            unassigned.append(order.order_id)
            block_detail = block_rejection_details.get(order.order_id, {})
            combined_reasons = [
                *rejected_reasons,
                *block_detail.get("candidate_rejection_reasons", []),
            ]
            reason = _primary_rejection_reason(combined_reasons)
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            rejection_details[order.order_id] = {
                "stage": "seed",
                "primary_reason": reason,
                "candidate_rejection_reasons": sorted(set(combined_reasons)),
            }
        else:
            _, route_idx, candidate = best
            plans[route_idx] = candidate

    solution = _solution_from_plans(
        plans,
        unassigned_order_ids=unassigned,
        vrp_input=vrp_input,
        status=SolverStatus.FEASIBLE,
    )
    summary = {
        "assigned_count": len(vrp_input.orders) - len(unassigned),
        "unassigned_count": len(unassigned),
        "total_generalized_cost": (
            solution.cost_breakdown.total_generalized_cost
            if solution.cost_breakdown is not None
            else 0
        ),
        "rejection_reasons": rejection_reasons,
        "rejection_details": rejection_details,
        "depot_seed_strategy": seed_strategy,
        "depot_seed_assigned_count": len(block_assigned_order_ids),
    }
    return solution, plans, summary


def _repair_unassigned_feasible(
    vrp_input: VRPInput,
    incumbent: VRPSolution,
    incumbent_plans: list[RoutePlan],
    feasibility: list[OrderFeasibility],
    *,
    reload_time_s: int,
    max_reload_count: int | None,
) -> tuple[VRPSolution, list[RoutePlan], ValidationResult, dict]:
    """Bounded global repair: try to insert still-unassigned feasible orders."""
    diag_by_order = {diag.order_id: diag for diag in feasibility}
    order_by_id = {order.order_id: order for order in vrp_input.orders}
    plans = list(incumbent_plans)
    remaining_unassigned = list(incumbent.unassigned_orders)
    improvements = 0
    attempted = 0
    passes = 0
    rejection_details: dict[str, dict] = {}

    while passes < 5:
        passes += 1
        pass_improvements = 0
        for order_id in list(remaining_unassigned):
            diag = diag_by_order.get(order_id)
            order = order_by_id.get(order_id)
            if order is None or diag is None or not diag.globally_feasible:
                continue
            attempted += 1
            eligible_vehicle_ids = set(diag.eligible_vehicle_ids)
            best: tuple[tuple, int, RoutePlan] | None = None
            rejected_reasons: list[str] = []
            for route_idx, plan in enumerate(plans):
                if eligible_vehicle_ids and plan.vehicle.courier_id not in eligible_vehicle_ids:
                    continue
                for specs in _candidate_specs_for_order(
                    plan,
                    order,
                    max_reload_count=max_reload_count,
                ):
                    candidate = simulate_route_plan(
                        vrp_input,
                        plan.vehicle,
                        specs,
                        reload_time_s=reload_time_s,
                        orders_by_id=order_by_id,
                    )
                    if not candidate.is_valid:
                        rejected_reasons.extend(_route_plan_rejection_reasons(candidate))
                        continue
                    score = _insertion_score(plan, candidate, plans)
                    if best is None or score < best[0]:
                        best = (score, route_idx, candidate)
            if best is None:
                rejection_details[order_id] = {
                    "stage": "repair_unassigned_feasible",
                    "primary_reason": _primary_rejection_reason(rejected_reasons),
                    "candidate_rejection_reasons": sorted(set(rejected_reasons)),
                }
                continue
            _, route_idx, candidate = best
            plans[route_idx] = candidate
            remaining_unassigned.remove(order_id)
            rejection_details.pop(order_id, None)
            improvements += 1
            pass_improvements += 1
        if pass_improvements == 0:
            break

    solution = _solution_from_plans(
        plans,
        unassigned_order_ids=remaining_unassigned,
        vrp_input=vrp_input,
        status=incumbent.solver_status,
    )
    validation = _validation_with_reload_limit(
        plans,
        max_reload_count=max_reload_count,
    )
    return (
        solution,
        plans,
        validation,
        {
            "attempted": attempted > 0,
            "passes": passes if attempted else 0,
            "improvements": improvements,
            "unassigned_feasible_attempted": attempted,
            "rejection_details": rejection_details,
        },
    )


def _build_spatial_sweep_depot_seed_plans(
    vrp_input: VRPInput,
    initial_plans: list[RoutePlan],
    diag_by_order: dict[str, OrderFeasibility],
    *,
    reload_time_s: int,
) -> tuple[list[RoutePlan], set[str], dict[str, dict]]:
    """Build a geography-first depot seed for large depot-pickup batches.

    The order-by-order insertion seed is intentionally conservative, but on
    large same-day batches it can mix distant deliveries into every courier's
    route.  This seed first gives each physical courier a compact angular
    territory, packs those orders into capacity-valid load blocks, and relies
    on the normal repair pass to fill anything left over globally.
    """
    depot_orders = [
        order
        for order in vrp_input.orders
        if order.pickup_is_depot
        and (diag_by_order.get(order.order_id) is None or diag_by_order[order.order_id].globally_feasible)
    ]
    if len(depot_orders) < 50 or not vrp_input.vehicles:
        return list(initial_plans), set(), {}

    orders_by_id = {order.order_id: order for order in vrp_input.orders}
    depot_order_ids = {order.order_id for order in depot_orders}
    assigned: set[str] = set()
    plans = list(initial_plans)
    vehicle_blocks: list[list[list[str]]] = [[] for _ in vrp_input.vehicles]
    remaining = set(depot_order_ids)
    rejection_details: dict[str, dict] = {}

    center_lat, center_lon = _spatial_center(vrp_input)
    angle_order = sorted(
        depot_orders,
        key=lambda order: (
            _location_angle(vrp_input, order.delivery_index, center_lat, center_lon),
            _location_radius(vrp_input, order.delivery_index, center_lat, center_lon),
            order.hard_latest_time_s or order.latest_time_s,
            order.order_id,
        ),
    )
    angle_order_ids = [order.order_id for order in angle_order]
    targets = _spatial_vehicle_targets(vrp_input, len(depot_orders))
    vehicle_order = sorted(
        range(len(vrp_input.vehicles)),
        key=lambda idx: (
            -targets[idx],
            -vrp_input.vehicles[idx].capacity_grams,
            vrp_input.vehicles[idx].start_time_s,
            vrp_input.vehicles[idx].courier_id,
        ),
    )

    cursor = 0
    for route_idx in vehicle_order:
        vehicle = vrp_input.vehicles[route_idx]
        target = targets[route_idx]
        if target <= 0 or not remaining:
            continue
        selected, cursor = _select_spatial_vehicle_orders(
            vrp_input,
            angle_order_ids,
            remaining,
            diag_by_order,
            vehicle,
            target=target,
            cursor=cursor,
        )
        if not selected:
            continue
        ordered = _nearest_order_sequence(vrp_input, vehicle, selected, orders_by_id)
        blocks = _pack_order_sequence_into_blocks(ordered, orders_by_id, vehicle)
        blocks, plan, removed = _trim_blocks_to_valid_route(
            vrp_input,
            vehicle,
            blocks,
            orders_by_id,
            reload_time_s=reload_time_s,
        )
        for order_id in removed:
            remaining.add(order_id)
        if not blocks or plan is None:
            continue
        vehicle_blocks[route_idx] = blocks
        plans[route_idx] = plan
        for block in blocks:
            assigned.update(block)

    plans, vehicle_blocks = _compact_depot_blocks(
        vrp_input,
        plans,
        vehicle_blocks,
        orders_by_id,
        reload_time_s=reload_time_s,
    )
    assigned = {order_id for blocks in vehicle_blocks for block in blocks for order_id in block}
    for order_id in depot_order_ids - assigned:
        rejection_details[order_id] = {
            "stage": "spatial_sweep_depot_seed",
            "primary_reason": "route_interaction_or_solver_budget",
            "candidate_rejection_reasons": ["spatial_seed_not_selected"],
        }
    return plans, assigned, rejection_details


def _build_depot_block_seed_plans(
    vrp_input: VRPInput,
    initial_plans: list[RoutePlan],
    diag_by_order: dict[str, OrderFeasibility],
    *,
    reload_time_s: int,
    max_reload_count: int | None,
) -> tuple[list[RoutePlan], set[str], dict[str, dict]]:
    """Pack depot-pickup work into explicit load blocks before local insertion.

    The local insertion path is good at small repairs but bad at discovering
    load-block structure from scratch: it tends to overfill the initial pickup
    prefix, then sees only capacity failures.  This constructor makes the
    reload decision explicit by maintaining per-vehicle blocks and simulating
    the whole route every time a block changes.
    """
    orders_by_id = {order.order_id: order for order in vrp_input.orders}
    vehicle_blocks: list[list[list[str]]] = [[] for _ in vrp_input.vehicles]
    plans = list(initial_plans)
    assigned: set[str] = set()
    rejection_details: dict[str, dict] = {}

    depot_orders = [
        order
        for order in sorted(vrp_input.orders, key=lambda o: _order_priority_key(o, diag_by_order))
        if order.pickup_is_depot
        and (diag_by_order.get(order.order_id) is None or diag_by_order[order.order_id].globally_feasible)
    ]

    for order in depot_orders:
        diag = diag_by_order.get(order.order_id)
        eligible_vehicle_ids = set(diag.eligible_vehicle_ids if diag else [])
        best: tuple[tuple, int, list[list[str]], RoutePlan] | None = None
        rejected_reasons: list[str] = []

        for route_idx in _candidate_route_indices_for_depot_order(
            vrp_input,
            order,
            diag,
            plans,
            limit=_BLOCK_SEED_ROUTE_CANDIDATES,
        ):
            vehicle = vrp_input.vehicles[route_idx]
            if eligible_vehicle_ids and vehicle.courier_id not in eligible_vehicle_ids:
                continue
            current_blocks = vehicle_blocks[route_idx]

            for block_idx in _candidate_block_indices_for_order(
                vrp_input,
                vehicle,
                current_blocks,
                order,
                orders_by_id,
                limit=_BLOCK_SEED_EXISTING_BLOCK_CANDIDATES,
            ):
                candidate_blocks = _copy_blocks_with_order(current_blocks, block_idx, order.order_id)
                if not _blocks_within_reload_limit(candidate_blocks, max_reload_count):
                    rejected_reasons.append("reload_limit")
                    continue
                if not _block_capacity_ok(candidate_blocks[block_idx], orders_by_id, vehicle):
                    rejected_reasons.append("capacity")
                    continue
                candidate = _simulate_blocks(
                    vrp_input,
                    vehicle,
                    candidate_blocks,
                    reload_time_s=reload_time_s,
                    orders_by_id=orders_by_id,
                )
                if not candidate.is_valid:
                    rejected_reasons.extend(_route_plan_rejection_reasons(candidate))
                    continue
                score = _block_seed_score(plans[route_idx], candidate, plans, candidate_blocks)
                if best is None or score < best[0]:
                    best = (score, route_idx, candidate_blocks, candidate)

            candidate_blocks = [list(block) for block in current_blocks] + [[order.order_id]]
            if not _blocks_within_reload_limit(candidate_blocks, max_reload_count):
                rejected_reasons.append("reload_limit")
            elif not _block_capacity_ok(candidate_blocks[-1], orders_by_id, vehicle):
                rejected_reasons.append("capacity")
            else:
                candidate = _simulate_blocks(
                    vrp_input,
                    vehicle,
                    candidate_blocks,
                    reload_time_s=reload_time_s,
                    orders_by_id=orders_by_id,
                )
                if not candidate.is_valid:
                    rejected_reasons.extend(_route_plan_rejection_reasons(candidate))
                else:
                    score = _block_seed_score(plans[route_idx], candidate, plans, candidate_blocks)
                    if best is None or score < best[0]:
                        best = (score, route_idx, candidate_blocks, candidate)

        if best is None:
            rejection_details[order.order_id] = {
                "stage": "depot_block_seed",
                "primary_reason": _primary_rejection_reason(rejected_reasons),
                "candidate_rejection_reasons": sorted(set(rejected_reasons)),
            }
            continue

        _, route_idx, chosen_blocks, chosen_plan = best
        vehicle_blocks[route_idx] = chosen_blocks
        plans[route_idx] = chosen_plan
        assigned.add(order.order_id)
        rejection_details.pop(order.order_id, None)

    return plans, assigned, rejection_details


def _copy_blocks_with_order(
    blocks: list[list[str]],
    block_idx: int,
    order_id: str,
) -> list[list[str]]:
    copied = [list(block) for block in blocks]
    copied[block_idx].append(order_id)
    return copied


def _seed_plan_score(plans: list[RoutePlan], assigned_order_ids: set[str]) -> tuple:
    return (
        -len(assigned_order_ids),
        sum(1 for plan in plans for stop in plan.stops if stop.stop_type == "DEPOT_RELOAD"),
        sum(plan.total_duration_s for plan in plans),
        sum(plan.total_distance_m for plan in plans),
    )


def _seed_candidates_to_finish(
    candidates: list[tuple[str, list[RoutePlan], set[str], dict[str, dict]]],
) -> list[tuple[str, list[RoutePlan], set[str], dict[str, dict]]]:
    if len(candidates) <= 1:
        return candidates
    ranked = sorted(candidates, key=lambda item: _seed_plan_score(item[1], item[2]))
    best = ranked[0]
    best_assigned = len(best[2])
    best_duration = sum(plan.total_duration_s for plan in best[1])
    to_finish = [best]
    for candidate in ranked[1:]:
        assigned = len(candidate[2])
        duration = sum(plan.total_duration_s for plan in candidate[1])
        assigned_gap = best_assigned - assigned
        materially_shorter = best_duration > 0 and duration < int(best_duration * 0.75)
        if assigned_gap <= 60 and materially_shorter:
            to_finish.append(candidate)
    return to_finish


def _spatial_center(vrp_input: VRPInput) -> tuple[float, float]:
    depot_locations = [
        vrp_input.locations[vehicle.depot_index]
        for vehicle in vrp_input.vehicles
        if 0 <= vehicle.depot_index < len(vrp_input.locations)
    ]
    if depot_locations:
        return (
            sum(lat for lat, _ in depot_locations) / len(depot_locations),
            sum(lon for _, lon in depot_locations) / len(depot_locations),
        )
    if not vrp_input.locations:
        return (0.0, 0.0)
    return (
        sum(lat for lat, _ in vrp_input.locations) / len(vrp_input.locations),
        sum(lon for _, lon in vrp_input.locations) / len(vrp_input.locations),
    )


def _location_angle(
    vrp_input: VRPInput,
    location_index: int,
    center_lat: float,
    center_lon: float,
) -> float:
    lat, lon = vrp_input.locations[location_index]
    return math.atan2(lat - center_lat, lon - center_lon)


def _location_radius(
    vrp_input: VRPInput,
    location_index: int,
    center_lat: float,
    center_lon: float,
) -> float:
    lat, lon = vrp_input.locations[location_index]
    return (lat - center_lat) ** 2 + (lon - center_lon) ** 2


def _spatial_vehicle_targets(vrp_input: VRPInput, order_count: int) -> list[int]:
    if not vrp_input.vehicles:
        return []
    weights: list[float] = []
    for vehicle in vrp_input.vehicles:
        capacity_kg = max(vehicle.capacity_grams / 1000, 1)
        shift_hours = max(
            ((vehicle.hard_end_time_s or vehicle.end_time_s) - vehicle.start_time_s) / 3600,
            1,
        )
        vehicle_type = getattr(vehicle.vehicle_type, "value", str(vehicle.vehicle_type))
        type_factor = 0.45 if vehicle_type == "MOTORCYCLE" else 1.0
        weights.append(math.sqrt(capacity_kg) * shift_hours * type_factor)
    total_weight = sum(weights) or 1
    raw_targets = [max(1, round(order_count * weight / total_weight)) for weight in weights]
    delta = order_count - sum(raw_targets)
    order = sorted(
        range(len(raw_targets)),
        key=lambda idx: (-weights[idx], vrp_input.vehicles[idx].courier_id),
    )
    cursor = 0
    while delta != 0 and order:
        idx = order[cursor % len(order)]
        if delta > 0:
            raw_targets[idx] += 1
            delta -= 1
        elif raw_targets[idx] > 1:
            raw_targets[idx] -= 1
            delta += 1
        cursor += 1
        if cursor > len(order) * max(order_count, 1):
            break
    return raw_targets


def _select_spatial_vehicle_orders(
    vrp_input: VRPInput,
    angle_order_ids: list[str],
    remaining: set[str],
    diag_by_order: dict[str, OrderFeasibility],
    vehicle: VehicleData,
    *,
    target: int,
    cursor: int,
) -> tuple[list[str], int]:
    if not angle_order_ids:
        return [], cursor
    selected: list[str] = []
    scans = 0
    max_scans = len(angle_order_ids) * 2
    while len(selected) < target and remaining and scans < max_scans:
        order_id = angle_order_ids[cursor % len(angle_order_ids)]
        cursor += 1
        scans += 1
        if order_id not in remaining:
            continue
        diag = diag_by_order.get(order_id)
        if diag is not None and diag.eligible_vehicle_ids and vehicle.courier_id not in diag.eligible_vehicle_ids:
            continue
        selected.append(order_id)
        remaining.remove(order_id)
    return selected, cursor


def _nearest_order_sequence(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    order_ids: list[str],
    orders_by_id: dict[str, OrderData],
) -> list[str]:
    matrix = _duration_matrix_for_seed(vrp_input, vehicle)
    remaining = set(order_ids)
    current = vehicle.depot_index
    ordered: list[str] = []
    while remaining:
        order_id = min(
            remaining,
            key=lambda candidate: (
                matrix[current][orders_by_id[candidate].delivery_index],
                orders_by_id[candidate].hard_latest_time_s or orders_by_id[candidate].latest_time_s,
                orders_by_id[candidate].earliest_time_s,
                candidate,
            ),
        )
        ordered.append(order_id)
        remaining.remove(order_id)
        current = orders_by_id[order_id].delivery_index
    return ordered


def _pack_order_sequence_into_blocks(
    order_ids: list[str],
    orders_by_id: dict[str, OrderData],
    vehicle: VehicleData,
) -> list[list[str]]:
    blocks: list[list[str]] = []
    for order_id in order_ids:
        if blocks and _block_capacity_ok([*blocks[-1], order_id], orders_by_id, vehicle):
            blocks[-1].append(order_id)
        elif _block_capacity_ok([order_id], orders_by_id, vehicle):
            blocks.append([order_id])
    return blocks


def _trim_blocks_to_valid_route(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    blocks: list[list[str]],
    orders_by_id: dict[str, OrderData],
    *,
    reload_time_s: int,
) -> tuple[list[list[str]], RoutePlan | None, list[str]]:
    candidate_blocks = [list(block) for block in blocks if block]
    removed: list[str] = []
    while candidate_blocks:
        plan = _simulate_blocks(
            vrp_input,
            vehicle,
            candidate_blocks,
            reload_time_s=reload_time_s,
            orders_by_id=orders_by_id,
        )
        if plan.is_valid:
            return candidate_blocks, plan, removed
        remove_id = _order_to_trim_from_invalid_blocks(plan, candidate_blocks)
        if remove_id is None:
            remove_id = candidate_blocks[-1][-1]
        removed.append(remove_id)
        candidate_blocks = _remove_order_from_blocks(candidate_blocks, remove_id)
    return [], None, removed


def _order_to_trim_from_invalid_blocks(
    plan: RoutePlan,
    blocks: list[list[str]],
) -> str | None:
    for violation in plan.violations:
        if violation.order_id:
            return violation.order_id
    for block in reversed(blocks):
        if block:
            return block[-1]
    return None


def _remove_order_from_blocks(blocks: list[list[str]], order_id: str) -> list[list[str]]:
    trimmed: list[list[str]] = []
    for block in blocks:
        next_block = [candidate for candidate in block if candidate != order_id]
        if next_block:
            trimmed.append(next_block)
    return trimmed


def _compact_depot_blocks(
    vrp_input: VRPInput,
    plans: list[RoutePlan],
    vehicle_blocks: list[list[list[str]]],
    orders_by_id: dict[str, OrderData],
    *,
    reload_time_s: int,
) -> tuple[list[RoutePlan], list[list[list[str]]]]:
    """Merge small depot load blocks when the full simulated route stays valid."""
    compacted_plans = list(plans)
    compacted_blocks = [[list(block) for block in blocks] for blocks in vehicle_blocks]

    for route_idx, vehicle in enumerate(vrp_input.vehicles):
        blocks = compacted_blocks[route_idx]
        if len(blocks) < 2:
            continue
        current_plan = compacted_plans[route_idx]
        improved = True
        passes = 0
        while improved and passes < 3:
            improved = False
            passes += 1
            source_indices = sorted(
                range(len(blocks)),
                key=lambda idx: (len(blocks[idx]), idx),
            )
            for source_idx in source_indices:
                if source_idx >= len(blocks) or len(blocks) > 1 and not blocks[source_idx]:
                    continue
                for order_id in list(blocks[source_idx]):
                    best: tuple[tuple, list[list[str]], RoutePlan] | None = None
                    for target_idx in range(len(blocks)):
                        if target_idx == source_idx:
                            continue
                        candidate_blocks = _move_order_between_blocks(
                            blocks,
                            order_id,
                            source_idx=source_idx,
                            target_idx=target_idx,
                        )
                        if candidate_blocks == blocks:
                            continue
                        effective_target_idx = _target_index_after_move(
                            blocks,
                            source_idx=source_idx,
                            target_idx=target_idx,
                        )
                        if not _block_capacity_ok(
                            candidate_blocks[effective_target_idx],
                            orders_by_id,
                            vehicle,
                        ):
                            continue
                        candidate_plan = _simulate_blocks(
                            vrp_input,
                            vehicle,
                            candidate_blocks,
                            reload_time_s=reload_time_s,
                            orders_by_id=orders_by_id,
                        )
                        if not candidate_plan.is_valid:
                            continue
                        score = _same_assignment_route_score(current_plan, candidate_plan)
                        if score >= _same_assignment_route_score(current_plan, current_plan):
                            continue
                        if best is None or score < best[0]:
                            best = (score, candidate_blocks, candidate_plan)
                    if best is None:
                        continue
                    _, blocks, current_plan = best
                    compacted_blocks[route_idx] = blocks
                    compacted_plans[route_idx] = current_plan
                    improved = True
                    break
                if improved:
                    break

    return compacted_plans, compacted_blocks


def _move_order_between_blocks(
    blocks: list[list[str]],
    order_id: str,
    *,
    source_idx: int,
    target_idx: int,
) -> list[list[str]]:
    candidate = [list(block) for block in blocks]
    if order_id not in candidate[source_idx]:
        return candidate
    candidate[source_idx].remove(order_id)
    if not candidate[source_idx]:
        del candidate[source_idx]
        if target_idx > source_idx:
            target_idx -= 1
    candidate[target_idx].append(order_id)
    return candidate


def _target_index_after_move(
    blocks: list[list[str]],
    *,
    source_idx: int,
    target_idx: int,
) -> int:
    if len(blocks[source_idx]) == 1 and target_idx > source_idx:
        return target_idx - 1
    return target_idx


def _same_assignment_route_score(previous: RoutePlan, candidate: RoutePlan) -> tuple:
    previous_reloads = sum(1 for stop in previous.stops if stop.stop_type == "DEPOT_RELOAD")
    candidate_reloads = sum(1 for stop in candidate.stops if stop.stop_type == "DEPOT_RELOAD")
    return (
        candidate_reloads,
        candidate.total_duration_s,
        candidate.route_end_time_s,
        candidate.total_distance_m,
    )


def _candidate_route_indices_for_depot_order(
    vrp_input: VRPInput,
    order: OrderData,
    diag: OrderFeasibility | None,
    plans: list[RoutePlan],
    *,
    limit: int,
) -> list[int]:
    allowed = set(diag.eligible_vehicle_ids if diag else [])
    eligible_indices = [
        idx
        for idx, vehicle in enumerate(vrp_input.vehicles)
        if not allowed or vehicle.courier_id in allowed
    ]
    if len(eligible_indices) <= limit:
        return eligible_indices

    scored: list[tuple[tuple, int]] = []
    for idx in eligible_indices:
        vehicle = vrp_input.vehicles[idx]
        matrix = _duration_matrix_for_seed(vrp_input, vehicle)
        delivery_count = sum(1 for stop in plans[idx].stops if stop.stop_type == "DELIVERY")
        try:
            travel = (
                matrix[vehicle.depot_index][order.pickup_index]
                + matrix[order.pickup_index][order.delivery_index]
            )
        except IndexError:
            travel = 10**9
        scored.append(
            (
                (
                    travel,
                    delivery_count,
                    -vehicle.capacity_grams,
                    plans[idx].route_end_time_s,
                    vehicle.courier_id,
                ),
                idx,
            )
        )

    largest_count = max(2, limit // 3)
    nearest_count = max(1, limit - largest_count)
    nearest = [idx for _, idx in sorted(scored)[:nearest_count]]
    largest = sorted(
        eligible_indices,
        key=lambda idx: (-vrp_input.vehicles[idx].capacity_grams, plans[idx].route_end_time_s),
    )[:largest_count]
    ordered = []
    for idx in [*nearest, *largest]:
        if idx not in ordered:
            ordered.append(idx)
    return ordered[:limit]


def _candidate_block_indices_for_order(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    blocks: list[list[str]],
    order: OrderData,
    orders_by_id: dict[str, OrderData],
    *,
    limit: int,
) -> list[int]:
    if len(blocks) <= limit:
        return list(range(len(blocks)))

    matrix = _duration_matrix_for_seed(vrp_input, vehicle)
    scored: list[tuple[tuple, int]] = []
    for idx, block in enumerate(blocks):
        if not _block_capacity_ok([*block, order.order_id], orders_by_id, vehicle):
            continue
        anchor_order = orders_by_id[block[-1]]
        try:
            travel = matrix[anchor_order.delivery_index][order.delivery_index]
        except IndexError:
            travel = 10**9
        remaining_capacity = vehicle.capacity_grams - sum(
            orders_by_id[order_id].weight_grams for order_id in block
        )
        scored.append(((travel, -remaining_capacity, len(block), idx), idx))
    return [idx for _, idx in sorted(scored)[:limit]]


def _blocks_within_reload_limit(
    blocks: list[list[str]],
    max_reload_count: int | None,
) -> bool:
    if max_reload_count is None:
        return True
    return max(0, len(blocks) - 1) <= max_reload_count


def _block_capacity_ok(
    block: list[str],
    orders_by_id: dict[str, OrderData],
    vehicle: VehicleData,
) -> bool:
    weight = sum(orders_by_id[order_id].weight_grams for order_id in block)
    volume = sum(orders_by_id[order_id].volume_cm3 for order_id in block)
    packages = sum(orders_by_id[order_id].package_count for order_id in block)
    if vehicle.capacity_grams > 0 and weight > vehicle.capacity_grams:
        return False
    if vehicle.capacity_volume_cm3 > 0 and volume > vehicle.capacity_volume_cm3:
        return False
    if vehicle.max_packages > 0 and packages > vehicle.max_packages:
        return False
    if vehicle.max_stops > 0 and len(block) > vehicle.max_stops:
        return False
    return True


def _simulate_blocks(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    blocks: list[list[str]],
    *,
    reload_time_s: int,
    orders_by_id: dict[str, OrderData] | None = None,
) -> RoutePlan:
    specs = _specs_from_blocks(vrp_input, vehicle, blocks, orders_by_id=orders_by_id)
    return simulate_route_plan(
        vrp_input,
        vehicle,
        specs,
        reload_time_s=reload_time_s,
        orders_by_id=orders_by_id,
    )


def _specs_from_blocks(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    blocks: list[list[str]],
    *,
    orders_by_id: dict[str, OrderData] | None = None,
) -> list[RoutePlanStopSpec]:
    orders_by_id = orders_by_id or {order.order_id: order for order in vrp_input.orders}
    specs: list[RoutePlanStopSpec] = []
    for block_idx, block in enumerate(blocks):
        if not block:
            continue
        pickup_order_ids = _sequence_block_pickups(vrp_input, vehicle, block, orders_by_id)
        reload_location = orders_by_id[pickup_order_ids[0]].pickup_index
        if block_idx > 0:
            specs.extend(build_depot_reload_specs(vehicle, reload_location))
        for order_id in pickup_order_ids:
            order = orders_by_id[order_id]
            specs.append(RoutePlanStopSpec(order.order_id, "PICKUP", order.pickup_index))
        delivery_start = orders_by_id[pickup_order_ids[-1]].pickup_index
        for order_id in _sequence_block_deliveries(
            vrp_input,
            vehicle,
            block,
            orders_by_id,
            start_location_index=delivery_start,
        ):
            order = orders_by_id[order_id]
            specs.append(RoutePlanStopSpec(order.order_id, "DELIVERY", order.delivery_index))
    return specs


def _sequence_block_pickups(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    block: list[str],
    orders_by_id: dict[str, OrderData],
) -> list[str]:
    matrix = _duration_matrix_for_seed(vrp_input, vehicle)
    remaining = set(block)
    current = vehicle.depot_index
    ordered: list[str] = []
    while remaining:
        order_id = min(
            remaining,
            key=lambda candidate: (
                matrix[current][orders_by_id[candidate].pickup_index],
                orders_by_id[candidate].pickup_index,
                candidate,
            ),
        )
        ordered.append(order_id)
        remaining.remove(order_id)
        current = orders_by_id[order_id].pickup_index
    return ordered


def _sequence_block_deliveries(
    vrp_input: VRPInput,
    vehicle: VehicleData,
    block: list[str],
    orders_by_id: dict[str, OrderData],
    *,
    start_location_index: int,
) -> list[str]:
    matrix = _duration_matrix_for_seed(vrp_input, vehicle)
    remaining = set(block)
    current = start_location_index
    ordered: list[str] = []
    while remaining:
        order_id = min(
            remaining,
            key=lambda candidate: (
                matrix[current][orders_by_id[candidate].delivery_index],
                orders_by_id[candidate].hard_latest_time_s or orders_by_id[candidate].latest_time_s,
                orders_by_id[candidate].earliest_time_s,
                candidate,
            ),
        )
        ordered.append(order_id)
        remaining.remove(order_id)
        current = orders_by_id[order_id].delivery_index
    return ordered


def _block_seed_score(
    previous: RoutePlan,
    candidate: RoutePlan,
    all_plans: list[RoutePlan],
    candidate_blocks: list[list[str]],
) -> tuple:
    added_duration = candidate.total_duration_s - previous.total_duration_s
    added_distance = candidate.total_distance_m - previous.total_distance_m
    current_reload_count = sum(1 for stop in previous.stops if stop.stop_type == "DEPOT_RELOAD")
    candidate_reload_count = max(0, len(candidate_blocks) - 1)
    added_reload_count = max(0, candidate_reload_count - current_reload_count)
    current_counts = [
        sum(1 for stop in plan.stops if stop.stop_type == "DELIVERY")
        for plan in all_plans
    ]
    candidate_delivery_count = sum(len(block) for block in candidate_blocks)
    load_balance_penalty = max(current_counts or [0]) - min(current_counts or [0])
    return (
        added_reload_count,
        candidate.route_end_time_s,
        added_duration,
        added_distance,
        load_balance_penalty,
        -candidate_delivery_count,
        len(candidate_blocks),
    )


def _duration_matrix_for_seed(
    vrp_input: VRPInput,
    vehicle: VehicleData,
) -> list[list[int]]:
    if vrp_input.duration_matrices:
        return vrp_input.duration_matrices.get(vehicle.osrm_profile, vrp_input.duration_matrix)
    return vrp_input.duration_matrix


def _candidate_specs_for_order(
    plan: RoutePlan,
    order: OrderData,
    *,
    max_reload_count: int | None,
) -> list[list[RoutePlanStopSpec]]:
    specs = plan.stop_specs
    candidates: list[list[RoutePlanStopSpec]] = []
    pickup_spec = RoutePlanStopSpec(order.order_id, "PICKUP", order.pickup_index)
    delivery_spec = RoutePlanStopSpec(order.order_id, "DELIVERY", order.delivery_index)
    current_reloads = sum(1 for spec in specs if spec.stop_type == "DEPOT_RELOAD")
    can_add_reload = max_reload_count is None or current_reloads < max_reload_count

    if _is_depot_pickup_order(order, plan.vehicle):
        pickup_positions = _depot_pickup_positions(specs, order.pickup_index)
        for pickup_pos in pickup_positions:
            with_pickup = specs[:pickup_pos] + [pickup_spec] + specs[pickup_pos:]
            for delivery_pos in _bounded_positions(len(with_pickup), start=pickup_pos + 1):
                candidates.append(
                    with_pickup[:delivery_pos] + [delivery_spec] + with_pickup[delivery_pos:]
                )
        if specs and can_add_reload:
            for reload_pos in _reload_insertion_positions(specs):
                with_reload_pickup = (
                    specs[:reload_pos]
                    + build_depot_reload_specs(plan.vehicle, order.pickup_index)
                    + [pickup_spec]
                    + specs[reload_pos:]
                )
                pickup_pos = reload_pos + 2
                for delivery_pos in _bounded_positions(len(with_reload_pickup), start=pickup_pos + 1):
                    candidates.append(
                        with_reload_pickup[:delivery_pos]
                        + [delivery_spec]
                        + with_reload_pickup[delivery_pos:]
                    )
        elif not specs:
            candidates.append([pickup_spec, delivery_spec])
        return candidates

    for pickup_pos in _bounded_positions(len(specs), start=0):
        with_pickup = specs[:pickup_pos] + [pickup_spec] + specs[pickup_pos:]
        for delivery_pos in _bounded_positions(len(with_pickup), start=pickup_pos + 1):
            candidates.append(
                with_pickup[:delivery_pos] + [delivery_spec] + with_pickup[delivery_pos:]
            )
    return candidates


def _route_plan_rejection_reasons(plan: RoutePlan) -> list[str]:
    if not plan.violations:
        return ["route_interaction_or_solver_budget"]
    return [_reason_from_violation(violation) for violation in plan.violations]


def _reason_from_violation(violation: Violation) -> str:
    mapping = {
        ViolationType.CAPACITY_EXCEEDED: "capacity",
        ViolationType.MAX_PACKAGES_EXCEEDED: "package_capacity",
        ViolationType.TIME_WINDOW_VIOLATED: "hard_window",
        ViolationType.SHIFT_EXCEEDED: "shift",
        ViolationType.MAX_STOPS_EXCEEDED: "max_stops",
        ViolationType.MAX_DISTANCE_EXCEEDED: "max_distance",
        ViolationType.PICKUP_AFTER_DELIVERY: "pickup_delivery_sequence",
        ViolationType.MISSING_PICKUP_OR_DELIVERY: "pickup_delivery_sequence",
        ViolationType.DUPLICATE_ORDER: "duplicate_order",
    }
    return mapping.get(violation.violation_type, violation.violation_type.value.lower())


def _primary_rejection_reason(reasons: list[str]) -> str:
    if not reasons:
        return "route_interaction_or_solver_budget"
    priority = [
        "hard_window",
        "shift",
        "max_distance",
        "max_stops",
        "capacity",
        "package_capacity",
        "pickup_delivery_sequence",
        "duplicate_order",
    ]
    counts = {reason: reasons.count(reason) for reason in set(reasons)}
    return min(counts, key=lambda reason: (priority.index(reason) if reason in priority else len(priority), -counts[reason], reason))


def _depot_pickup_positions(
    specs: list[RoutePlanStopSpec],
    pickup_index: int,
) -> list[int]:
    if not specs:
        return [0]
    positions = [0]
    initial_block_open = True
    for idx, spec in enumerate(specs):
        if initial_block_open and spec.stop_type == "PICKUP":
            positions.append(idx + 1)
            continue
        initial_block_open = False
        if spec.stop_type == "DEPOT_RELOAD" and spec.location_index == pickup_index or spec.stop_type == "PICKUP" and spec.location_index == pickup_index:
            positions.append(idx + 1)
    return sorted(set(positions))[-_BOUNDED_INSERTION_POSITIONS:]


def _reload_insertion_positions(specs: list[RoutePlanStopSpec]) -> list[int]:
    positions = {len(specs)}
    for idx, spec in enumerate(specs):
        if spec.stop_type == "DELIVERY":
            positions.add(idx + 1)
    ordered = sorted(positions)
    if len(ordered) <= _BOUNDED_INSERTION_POSITIONS:
        return ordered
    return ordered[:4] + ordered[-(_BOUNDED_INSERTION_POSITIONS - 4):]


def _is_depot_pickup_order(order: OrderData, vehicle: VehicleData) -> bool:
    """True when the order's pickup happens at one of the vehicle's
    allowed depots — not only the home `depot_index`. Multi-depot
    couriers that have additional reload depots in `allowed_depot_indices`
    should treat pickups at any of those locations as depot pickups
    (no per-pickup service time, no soft-arrival enforcement)."""
    if order.pickup_is_depot:
        return True
    allowed = vehicle.allowed_depot_indices or (vehicle.depot_index,)
    return order.pickup_index in allowed


def _bounded_positions(length: int, *, start: int) -> list[int]:
    positions = list(range(start, length + 1))
    if len(positions) <= _BOUNDED_INSERTION_POSITIONS:
        return positions
    return positions[:4] + positions[-(_BOUNDED_INSERTION_POSITIONS - 4):]


def _insertion_score(
    previous: RoutePlan,
    candidate: RoutePlan,
    all_plans: list[RoutePlan],
) -> tuple:
    added_cost = _cost(candidate) - _cost(previous)
    added_duration = candidate.total_duration_s - previous.total_duration_s
    added_distance = candidate.total_distance_m - previous.total_distance_m
    current_counts = [
        sum(1 for stop in plan.stops if stop.stop_type == "DELIVERY")
        for plan in all_plans
    ]
    load_balance_penalty = max(current_counts or [0]) - min(current_counts or [0])
    hard_lateness = sum(
        1
        for violation in candidate.violations
        if violation.violation_type in (ViolationType.TIME_WINDOW_VIOLATED, ViolationType.SHIFT_EXCEEDED)
        and violation.severity == "error"
    )
    return (
        False,
        hard_lateness,
        added_cost,
        added_duration,
        added_distance,
        load_balance_penalty,
    )


def _with_lexicographic_unserved_penalties(vrp_input: VRPInput) -> VRPInput:
    upper_bound = _secondary_cost_upper_bound(vrp_input)
    high_penalty = max(upper_bound + 1, 1_000_000)
    return replace(
        vrp_input,
        orders=[
            replace(order, unserved_penalty=high_penalty, priority_penalty_multiplier=1.0)
            for order in vrp_input.orders
        ],
    )


def _secondary_cost_upper_bound(vrp_input: VRPInput) -> int:
    max_arc_cost = 0
    for vehicle in vrp_input.vehicles:
        per_km = vehicle.distance_cost_per_km + vehicle.fuel_cost_per_km + 1
        for row in vrp_input.distance_matrix:
            if row:
                max_arc_cost = max(max_arc_cost, int(max(row) * per_km / 1000))
        max_arc_cost += max(vehicle.fixed_route_cost, 0)
        max_arc_cost += max(vehicle.labor_cost_per_minute, 0) * 24 * 60
        max_arc_cost += max(vehicle.overtime_penalty_per_minute + vehicle.overtime_cost_per_minute, 0) * 24 * 60
    return max(1, (len(vrp_input.orders) * 4 + len(vrp_input.vehicles)) * max_arc_cost * _COST_SCALE)


def _initial_routes_from_plans(
    vrp_input: VRPInput,
    plans: list[RoutePlan],
) -> list[list[int]]:
    order_idx_by_id = {order.order_id: idx for idx, order in enumerate(vrp_input.orders)}
    n_depots = len(vrp_input.vehicles)
    routes: list[list[int]] = []
    for plan in plans:
        route_nodes: list[int] = []
        for spec in plan.stop_specs:
            if spec.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD") or spec.order_id is None:
                continue
            order_idx = order_idx_by_id.get(spec.order_id)
            if order_idx is None:
                continue
            route_nodes.append(n_depots + 2 * order_idx + (0 if spec.stop_type == "PICKUP" else 1))
        routes.append(route_nodes)
    return routes


def _normalize_reload_solution(
    vrp_input: VRPInput,
    raw_solution: VRPSolution,
    *,
    reload_time_s: int,
) -> tuple[VRPSolution, list[RoutePlan]]:
    vehicle_by_id = {vehicle.courier_id: vehicle for vehicle in vrp_input.vehicles}
    order_by_id = {order.order_id: order for order in vrp_input.orders}
    plans: list[RoutePlan] = []
    for route in raw_solution.routes:
        vehicle = vehicle_by_id.get(route.courier_id)
        if vehicle is None:
            continue
        specs: list[RoutePlanStopSpec] = []
        last_visible_loc = vehicle.depot_index
        for stop in route.stops:
            if stop.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD"):
                continue
            order = order_by_id.get(stop.order_id)
            if (
                stop.stop_type == "PICKUP"
                and order is not None
                and _is_depot_pickup_order(order, vehicle)
                and specs
                and last_visible_loc != stop.location_index
            ):
                specs.extend(build_depot_reload_specs(vehicle, stop.location_index))
            specs.append(
                RoutePlanStopSpec(
                    order_id=stop.order_id,
                    stop_type=stop.stop_type,
                    location_index=stop.location_index,
                )
            )
            last_visible_loc = stop.location_index
        plans.append(
            simulate_route_plan(
                vrp_input,
                vehicle,
                specs,
                reload_time_s=reload_time_s,
                orders_by_id=order_by_id,
            )
        )
    return (
        _solution_from_plans(
            plans,
            unassigned_order_ids=list(raw_solution.unassigned_orders),
            vrp_input=vrp_input,
            status=raw_solution.solver_status,
        ),
        plans,
    )


def _solution_from_plans(
    plans: list[RoutePlan],
    *,
    unassigned_order_ids: list[str],
    vrp_input: VRPInput,
    status: SolverStatus,
) -> VRPSolution:
    routes = [
        plan.to_planned_route(vrp_input.locations)
        for plan in plans
        if plan.stops
    ]
    assigned = {
        stop.order_id
        for route in routes
        for stop in route.stops
        if stop.stop_type == "DELIVERY" and stop.order_id
    }
    all_order_ids = {order.order_id for order in vrp_input.orders}
    unassigned = sorted((all_order_ids - assigned) | set(unassigned_order_ids))
    return VRPSolution(
        routes=routes,
        unassigned_orders=unassigned,
        total_distance_m=sum(route.total_distance_m for route in routes),
        total_duration_s=sum(route.total_duration_s for route in routes),
        solver_status=status,
        cost_breakdown=build_solution_cost_breakdown(
            routes=routes,
            unassigned_order_ids=unassigned,
            orders_by_id={order.order_id: order for order in vrp_input.orders},
        ),
    )


def _validation_with_reload_limit(
    plans: list[RoutePlan],
    *,
    max_reload_count: int | None,
) -> ValidationResult:
    result = validation_from_route_plans(plans)
    if max_reload_count is None:
        return result
    extra = list(result.violations)
    for plan in plans:
        reload_count = sum(1 for stop in plan.stops if stop.stop_type == "DEPOT_RELOAD")
        if reload_count > max_reload_count:
            extra.append(
                Violation(
                    violation_type=ViolationType.MAX_STOPS_EXCEEDED,
                    route_courier_id=plan.courier_id,
                    order_id=None,
                    message=f"Route has {reload_count} depot reloads, exceeds max {max_reload_count}",
                )
            )
    routes_with_violations = {v.route_courier_id for v in extra}
    return ValidationResult(
        is_valid=all(v.severity != "error" for v in extra),
        violations=extra,
        routes_checked=len(plans),
        routes_with_violations=len(routes_with_violations),
    )


def _solution_key(
    solution: VRPSolution,
    validation: ValidationResult,
    feasible_order_ids: set[str],
) -> tuple:
    unassigned_feasible = len(set(solution.unassigned_orders) & feasible_order_ids)
    hard_late_or_shift = sum(
        1
        for violation in validation.violations
        if violation.violation_type in (ViolationType.TIME_WINDOW_VIOLATED, ViolationType.SHIFT_EXCEEDED)
        and violation.severity == "error"
    )
    cost = solution.cost_breakdown.total_generalized_cost if solution.cost_breakdown else 0
    return (
        unassigned_feasible,
        validation.error_count,
        hard_late_or_shift,
        cost,
        solution.total_duration_s,
        solution.total_distance_m,
        len(solution.routes),
    )


def _candidate_improves(
    *,
    candidate: VRPSolution,
    candidate_validation: ValidationResult,
    incumbent: VRPSolution,
    incumbent_validation: ValidationResult,
    feasible_order_ids: set[str],
) -> bool:
    if incumbent_validation.error_count == 0 and candidate_validation.error_count > 0:
        return False
    return _solution_key(candidate, candidate_validation, feasible_order_ids) < _solution_key(
        incumbent,
        incumbent_validation,
        feasible_order_ids,
    )


def _classify_unassigned(
    solution: VRPSolution,
    feasibility: list[OrderFeasibility],
) -> dict:
    diag_by_order = {diag.order_id: diag for diag in feasibility}
    counts = {
        "globally_infeasible": 0,
        "route_interaction_or_solver_budget": 0,
        "unknown": 0,
    }
    for order_id in solution.unassigned_orders:
        diag = diag_by_order.get(order_id)
        if diag is None:
            counts["unknown"] += 1
        elif not diag.globally_feasible:
            counts["globally_infeasible"] += 1
        else:
            counts["route_interaction_or_solver_budget"] += 1
    return counts


def _stage_assignment_counts(
    *,
    vrp_input: VRPInput,
    feasibility: list[OrderFeasibility],
    seed_solution: VRPSolution,
    ortools_summary: dict,
    repaired_solution: VRPSolution,
    final_solution: VRPSolution,
) -> dict:
    total_orders = len(vrp_input.orders)
    globally_feasible = sum(1 for diag in feasibility if diag.globally_feasible)
    return {
        "input_orders": total_orders,
        "globally_feasible": globally_feasible,
        "globally_infeasible": total_orders - globally_feasible,
        "seed_assigned": total_orders - len(seed_solution.unassigned_orders),
        "ortools_assigned": (
            total_orders - ortools_summary["normalized_unassigned_count"]
            if "normalized_unassigned_count" in ortools_summary
            else None
        ),
        "repair_assigned": total_orders - len(repaired_solution.unassigned_orders),
        "final_assigned": total_orders - len(final_solution.unassigned_orders),
        "final_unassigned": len(final_solution.unassigned_orders),
    }


def _unassigned_order_diagnostics(
    solution: VRPSolution,
    feasibility: list[OrderFeasibility],
    seed_rejections: dict,
    repair_rejections: dict,
) -> list[dict]:
    diag_by_order = {diag.order_id: diag for diag in feasibility}
    details: list[dict] = []
    for order_id in solution.unassigned_orders:
        feasibility_diag = diag_by_order.get(order_id)
        repair_detail = repair_rejections.get(order_id)
        seed_detail = seed_rejections.get(order_id)
        if feasibility_diag is None:
            details.append(
                {
                    "order_id": order_id,
                    "category": "unknown",
                    "primary_reason": "unknown",
                    "globally_feasible": None,
                    "blocking_reasons": [],
                    "candidate_rejection_reasons": [],
                }
            )
            continue
        if not feasibility_diag.globally_feasible:
            details.append(
                {
                    "order_id": order_id,
                    "category": "globally_infeasible",
                    "primary_reason": (feasibility_diag.blocking_reasons or ["globally_infeasible"])[0],
                    "globally_feasible": False,
                    "blocking_reasons": feasibility_diag.blocking_reasons,
                    "eligible_vehicle_ids": feasibility_diag.eligible_vehicle_ids,
                    "candidate_rejection_reasons": [],
                }
            )
            continue
        chosen_detail = repair_detail or seed_detail or {}
        details.append(
            {
                "order_id": order_id,
                "category": "route_interaction_or_solver_budget",
                "primary_reason": chosen_detail.get("primary_reason", "route_interaction_or_solver_budget"),
                "globally_feasible": True,
                "blocking_reasons": [],
                "eligible_vehicle_ids": feasibility_diag.eligible_vehicle_ids,
                "candidate_rejection_reasons": chosen_detail.get("candidate_rejection_reasons", []),
            }
        )
    return details


def _reload_summary(solution: VRPSolution) -> dict:
    per_route = {}
    total = 0
    for route in solution.routes:
        count = sum(1 for stop in route.stops if stop.stop_type == "DEPOT_RELOAD")
        per_route[route.courier_id] = count
        total += count
    return {"total_reload_count": total, "per_route": per_route}


def _cost(plan: RoutePlan) -> int:
    if plan.cost_breakdown is None:
        return 0
    return plan.cost_breakdown.total_generalized_cost


def _order_priority_key(
    order: OrderData,
    diag_by_order: dict[str, OrderFeasibility],
) -> tuple:
    priority_rank = {
        OrderPriority.URGENT: 0,
        OrderPriority.HIGH: 1,
        OrderPriority.NORMAL: 2,
        OrderPriority.LOW: 3,
    }.get(order.priority, 2)
    diag = diag_by_order.get(order.order_id)
    eligible_count = len(diag.eligible_vehicle_ids) if diag is not None else 10**6
    pickup_latest_key = (
        order.pickup_latest_time_s
        if order.pickup_latest_time_s is not None and not order.pickup_is_depot
        else 24 * 3600
    )
    return (
        priority_rank,
        order.hard_latest_time_s if order.hard_latest_time_s is not None else 24 * 3600,
        order.latest_time_s,
        pickup_latest_key,
        eligible_count,
        -order.weight_grams,
        order.order_id,
    )
