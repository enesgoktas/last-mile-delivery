# Rich VRP Rewrite — Implementation Plan

> **Source of truth for technical content**: [deep-research-report.md](deep-research-report.md) at the project root (the research report). This plan operationalizes that report into discrete, ordered, file-level work items. Read the report first; this plan does not repeat its math or literature review.
>
> **Companion document**: this plan and the research report are companions — keep them together. Both live at the project root.

---

## Context

The current routing layer at [backend/app/algorithms/](backend/app/algorithms/) ships with two solvers:

1. **Human-dispatcher baseline** at [backend/app/algorithms/human_dispatcher_baseline.py:127](backend/app/algorithms/human_dispatcher_baseline.py:127) — a deterministic, human-style sweep heuristic. Used as the "what would a competent manual dispatcher do" baseline.
2. **OR-Tools CVRPTW optimizer** at [backend/app/algorithms/vrp_solver.py:301](backend/app/algorithms/vrp_solver.py:301) — Google OR-Tools with insertion seeds, two-opt polish, and a greedy fallback when search fails.

The goal of this project is **NOT to replace those two solvers**. Both must continue to function exactly as they do today, because they are the benchmarks against which the new solver will be measured. Specifically:

- The "Run Comparison" button must keep running its current 5-scenario comparison (1 human-dispatcher + 4 OR-Tools variants) on the same day's data side-by-side. After Milestone 1 it will gain new scenarios for the rich-VRP solver, but the 5 existing scenarios stay.
- The "Optimally Assign" button currently runs OR-Tools. It will be rewired to call the new rich-VRP solver. OR-Tools remains accessible as a fallback path inside the new solver and as a comparison scenario.

The new solver follows the architecture in `deep-research-report.md`:

> **Decomposed rich ALNS engine hybridized with HGS-style population search, route-pool set-partitioning recombination, and exact CP-SAT micro-repair, with OR-Tools retained as fallback.**

The total scope estimated by the report is ~40 person-weeks. This plan partitions it into 9 milestones (M0–M8) so that:

- **M0** ships visible plumbing on day one (new solver adapter wired, but internally delegates to OR-Tools — identical behavior, decoupled call site).
- **M1–M3** build a working pure-Python rich ALNS that already beats OR-Tools on most internal benchmarks for ≤200 orders.
- **M4–M5** bring it to research-grade quality via HGS population mechanics and SP route-pool recombination.
- **M6** adds decomposition to scale beyond ~2k orders.
- **M7** moves the local-search hot path into a compiled language for production performance.
- **M8** is rollout, telemetry, and SLA hardening.

The implementing agent should treat each milestone as a self-contained piece of work with its own acceptance criteria. Do not skip ahead. Do not start implementing later milestones before the current one passes its acceptance test. Do not delete or rename the existing OR-Tools or human-dispatcher solvers at any point.

---

## Status and completion tracking

This plan is the canonical reference for the rich-VRP rewrite — the project's "Rosetta stone." The implementing agent maintains it as work progresses. The protocol is strict:

- **Every completed sub-task flips its checkbox from `[ ]` to `[x]` in the same commit that completes the work.** Not before, not after — same commit.
- **Do not start a sub-task whose checkbox is already checked.** If the work needs to be redone (bug, regression, scope change), revert the checkbox to `[ ]` in a commit that explains why; only then start over.
- **Milestone-level checkboxes flip only when every sub-task under that milestone is checked AND the milestone's acceptance gate passes.** Demo to the user, then flip.
- **If a sub-task's scope changes mid-flight**, edit the sub-task description in the same commit so the plan stays accurate. The plan is the truth, not the commit log.
- **Never delete a completed sub-task** even if the underlying code is later replaced. The history of what was done matters; rewrite or annotate, don't erase.

### Milestone progress

- [x] **M0** — Solver registry, adapter wiring, "Optimally Assign" rerouted through `RichALNSAdapter` (delegates to OR-Tools)
- [x] **M1** — Foundation: domain layer, lexicographic objective, feasibility kernel, benchmark harness, greedy seeder
- [x] **M2** — Core ALNS engine
- [x] **M3** — Local search and candidate lists
- [x] **M4** — HGS hybridization *(Phase B production flip happens here)*
- [x] **M5** — Route-pool SP recombination & CP-SAT micro-repair
- [x] **M6** — Decomposition, rolling horizon, sparse global graph
- [x] **M7** — Compiled evaluator
- [x] **M8** — Production hardening
- [x] **M9** — Decommission OR-Tools from the runtime path *(Phase C)*

Each milestone section below lists its own sub-tasks with their own checkboxes. Update both levels — sub-tasks as you finish them, milestones once their acceptance gate passes.

---

## Decisions and assumptions

These are the choices that shape the rest of the plan. If the user disagrees with any, change them here first before implementing — every milestone references these decisions.

### D1 — Module layout

The new solver lives entirely under a new package:

```
backend/app/algorithms/rich_vrp/
```

The existing modules ([backend/app/algorithms/vrp_solver.py](backend/app/algorithms/vrp_solver.py), [backend/app/algorithms/human_dispatcher_baseline.py](backend/app/algorithms/human_dispatcher_baseline.py), [backend/app/algorithms/two_opt.py](backend/app/algorithms/two_opt.py), etc.) are not moved or renamed. The `solver_adapter.py` protocol module lives at [backend/app/algorithms/solver_adapter.py](backend/app/algorithms/solver_adapter.py) (i.e. one level above `rich_vrp/`) because it is shared by all three solvers (human, ortools, rich).

Why not the report's `backend/app/routing/` layout: keeping the new solver under `algorithms/` minimizes import churn and avoids touching unrelated modules. The Python namespace is internal — what matters is the subpackage discipline inside `rich_vrp/`.

### D2 — Compiled evaluator timing

Milestones 1–4 implement everything in **pure Python with numpy**. No Cython, no Rust, no PyO3. The report is unambiguous that a compiled evaluator is required for production-scale performance, but jumping straight to Cython before the algorithm is correct couples two large risks (algorithmic and FFI) that should be separated.

The compiled evaluator is **Milestone 7**, after correctness is established by passing unit, property, and benchmark tests on the pure Python build. Before M7, the pure Python evaluator runs slowly but correctly; benchmarks will reflect this — it's expected.

If the user wants the compiled evaluator earlier, swap M5/M6 with M7. Do not interleave: a half-Python/half-Cython evaluator is the worst of both.

### D3 — When the "Optimally Assign" button starts using the new solver

Three-phase rollout to keep production safe and to *eventually retire OR-Tools from the runtime path entirely*. The 80% benchmark gate at Phase B is a **one-time check**, not a per-solve cost — it does not run on production traffic. The OR-Tools *fallback* inside `RichALNSAdapter` only fires when the new engine actually fails; in the happy path it never runs.

- **Phase A (M0 onward)**: The button is rewired to call `RichALNSAdapter.solve(...)`. Internally, that adapter delegates 100% to the existing OR-Tools `solve()` for now. User-visible behavior is identical to today; what changes is the call path so that future swaps are surgical.
- **Phase B (after M4 passes its benchmark gate)**: Inside `RichALNSAdapter.solve(...)`, replace the OR-Tools delegation with the real rich-VRP engine. OR-Tools becomes the fallback when the new solver returns `SolverStatus.NOT_FOUND`, when its time budget is exceeded, or when the input fails the new solver's `supports()` check.
- **Phase C (after M9 decommission gate)**: OR-Tools is removed from the runtime path entirely. `RichALNSAdapter` no longer instantiates `ORToolsCVRPTWAdapter` internally. OR-Tools survives only as a comparison-overlay scenario in `SCENARIO_DEFINITIONS` and as a benchmark adapter in `bench/runner.py` — it is never on the production solve path.

Phases B and C are each gated on quantitative criteria, not on the calendar.

### D4 — Comparison scenarios

The existing 5 scenarios in [backend/app/services/route_scenario_service.py:75-106](backend/app/services/route_scenario_service.py:75) (`SCENARIO_DEFINITIONS`) are unchanged. We add scenarios to that tuple as the new solver matures:

- After M1: `rich_alns_service_first` (returns identical results to OR-Tools `service_first_full_fleet` until M4 — purpose is end-to-end plumbing test).
- After M4: `rich_alns_service_first` switches to the real ALNS engine; we add `rich_alns_fuel_aware`.
- After M5: SP recombination version exposed as `rich_alns_sp_polished`.

Each new scenario gets the same `key`, `label`, `routing_objective`, `fleet_utilization` schema as the existing definitions. Do not add new schema fields for the new scenarios.

### D5 — Solver adapter protocol

Introduce `SolverAdapter` (Python `Protocol`) at [backend/app/algorithms/solver_adapter.py](backend/app/algorithms/solver_adapter.py). All three solvers conform:

- `HumanDispatcherBaselineAdapter` — wraps [solve_human_dispatcher_baseline](backend/app/algorithms/human_dispatcher_baseline.py:127).
- `ORToolsCVRPTWAdapter` — wraps [vrp_solver.solve](backend/app/algorithms/vrp_solver.py:301).
- `RichALNSAdapter` — the new solver. From M0 it just delegates to ORToolsCVRPTWAdapter; from M4 it owns its own engine.

The protocol is the **only** API the rest of the codebase uses to call solvers. Existing call sites (see Milestone 0) are migrated to go through the adapter, not through `vrp_solver.solve` or `solve_human_dispatcher_baseline` directly. Leave the underlying free-function entrypoints intact — adapters import and call them.

### D6 — Lexicographic vs weighted objective

The current cost model at [vrp_solver.py:1092](backend/app/algorithms/vrp_solver.py:1092) (`_build_route_cost_breakdown` / `build_route_cost_breakdown`) sums weighted components. The research report mandates lexicographic ordering for the new solver: **(unserved) >> (lateness + overtime) >> (economic cost) >> (route stability)**.

The new solver implements lexicographic comparison internally. For reporting and scenario comparison, it still emits the same `EconomicCostBreakdown` shape (so the frontend doesn't change), but its internal best-of comparisons use the lexicographic tuple. The OR-Tools adapter and human-dispatcher adapter are not changed — they remain weighted.

### D7 — Scope guards

These items from the research report are **explicitly out of scope** for this plan:

- Time-dependent travel times (TD-VRP) beyond the existing `TIME_DEPENDENT_TRAVEL_ENABLED` hooks in [dispatch_service.py:796](backend/app/services/dispatch_service.py:796). The new solver consumes static matrices and the existing TD refinement pass continues to work as it does today.
- Learned operator selection / RL-based controllers. M3 implements segmented roulette wheel and α-UCB only.
- Automatic GPU usage. CPU-only.
- New persistence schemas beyond what M0 adds (`solver_kind` column on `RouteScenario`).

If the implementing agent finds itself reaching for any of the above, stop and ask the user.

---

## Existing code map (anchors for the implementation)

These are the files the new code interacts with. The implementing agent must read each one before touching it.

| Concern | File | Key symbols |
|---|---|---|
| OR-Tools solver entry | [backend/app/algorithms/vrp_solver.py](backend/app/algorithms/vrp_solver.py) | `solve()` at line 301; `OrderData`, `VehicleData`, `VRPInput`, `VRPSolution`, `PlannedRoute`, `PlannedStop`, `EconomicCostBreakdown`, `SolverStatus` (lines 33–162); `build_route_cost_breakdown`, `build_solution_cost_breakdown` |
| Human-dispatcher baseline | [backend/app/algorithms/human_dispatcher_baseline.py](backend/app/algorithms/human_dispatcher_baseline.py) | `solve_human_dispatcher_baseline()` at line 127; `build_sweep_zones()` at line 68 |
| Two-opt polish | [backend/app/algorithms/two_opt.py](backend/app/algorithms/two_opt.py) | `two_opt_improve()` at line 36 |
| OSRM matrix client | [backend/app/algorithms/osrm_client.py](backend/app/algorithms/osrm_client.py) | `get_distance_matrix()` at line 418; `MatrixResult`; `OSRM_PROFILE_BASE_URLS` at line 82 |
| Warm-start cache | [backend/app/algorithms/warm_start_cache.py](backend/app/algorithms/warm_start_cache.py) | `cache_solution()` at line 85; `build_initial_routes()` at line 122; `CachedAssignment` |
| Solution validator | [backend/app/algorithms/solution_validator.py](backend/app/algorithms/solution_validator.py) | `validate_solution()` at line 93; `ValidationResult` |
| Constraint diagnostics | [backend/app/algorithms/constraint_diagnostics.py](backend/app/algorithms/constraint_diagnostics.py) | full module |
| Insertion heuristic | [backend/app/algorithms/insertion_heuristic.py](backend/app/algorithms/insertion_heuristic.py) | full module — reuse for the M1 greedy seeder |
| Geo-clustering | [backend/app/algorithms/geo_clustering.py](backend/app/algorithms/geo_clustering.py) | `cluster_orders()` — reuse for M6 decomposition |
| Greedy fallback | [backend/app/algorithms/greedy_fallback.py](backend/app/algorithms/greedy_fallback.py) | full module |
| API: assign-optimal | [backend/app/api/dispatch.py:247](backend/app/api/dispatch.py:247) | `assign_optimal()` — enqueues `run_batch_optimization` |
| API: scenario comparison | [backend/app/api/dispatch.py:299](backend/app/api/dispatch.py:299) | `create_route_scenario_comparison()` — enqueues `run_route_scenario_comparison` |
| Celery: batch optimization | [backend/app/workers/optimization_tasks.py:112](backend/app/workers/optimization_tasks.py:112) | `run_batch_optimization()` |
| Celery: scenario comparison | [backend/app/workers/optimization_tasks.py:235](backend/app/workers/optimization_tasks.py:235) | `run_route_scenario_comparison()` |
| Dispatch service | [backend/app/services/dispatch_service.py](backend/app/services/dispatch_service.py) | `_solve_batch_variant()` at line 631; `_solve_human_dispatcher_baseline_variant()` at line 889 |
| Scenario service | [backend/app/services/route_scenario_service.py](backend/app/services/route_scenario_service.py) | `SCENARIO_DEFINITIONS` at line 75; `run_comparison()` at line 287; `_solve_scenario_vrp()` at line 736 |
| DB: routes | [backend/app/models/route.py](backend/app/models/route.py) | `Route`, `RouteStop`, `RouteStatus` |
| DB: scenarios | [backend/app/models/route_scenario.py](backend/app/models/route_scenario.py) | `RouteScenarioComparison`, `RouteScenario` |
| Result schema | [backend/app/schemas/route_scenario.py:12](backend/app/schemas/route_scenario.py:12) | `RouteScenarioSummary` |
| Frontend: Optimally Assign | [frontend/src/hooks/useOptimalAssign.ts](frontend/src/hooks/useOptimalAssign.ts) | calls `triggerAssignOptimal()` |
| Frontend: Comparison overlay | [frontend/src/pages/OrdersPage.tsx:216](frontend/src/pages/OrdersPage.tsx:216) | `RouteScenarioComparisonOverlay` |
| Frontend: API client | [frontend/src/api/client.ts](frontend/src/api/client.ts) | `triggerAssignOptimal`, `createRouteScenarioComparison`, `getRouteScenarioComparison` |
| Tests: solver | [backend/tests/unit/test_vrp_solver.py](backend/tests/unit/test_vrp_solver.py) | small synthetic instances |
| Tests: benchmark | [backend/tests/unit/test_vrp_benchmark_regressions.py](backend/tests/unit/test_vrp_benchmark_regressions.py) | regression suite |
| Tests: dispatch selection | [backend/tests/unit/test_dispatch_solver_selection.py](backend/tests/unit/test_dispatch_solver_selection.py) | reference for how solvers are wired |
| Build config | [backend/pyproject.toml](backend/pyproject.toml) | pytest, ruff, dependencies |

---

## Solver adapter contract

The contract is shared by all milestones — implement once in M0 and never change its surface. Definition lives at [backend/app/algorithms/solver_adapter.py](backend/app/algorithms/solver_adapter.py).

```python
"""Solver adapter protocol: the only API external code uses to invoke a VRP solver."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.algorithms.vrp_solver import VRPInput, VRPSolution


@dataclass(frozen=True, slots=True)
class SolveOptions:
    time_budget_s: float = 60.0
    seed: int = 0
    spread_fleet: bool = True
    skip_multi_trip: bool = False
    initial_routes: list[list[int]] | None = None  # warm start, OR-Tools-shape node sequences
    # Scenario-style flags accepted by every adapter; ignored by adapters that don't apply them
    routing_objective: str = "SERVICE_FIRST"
    fleet_utilization: str = "FULL_FLEET"


@dataclass(frozen=True, slots=True)
class SolveResult:
    solution: VRPSolution
    solver_kind: str          # one of "human_dispatcher", "ortools_cvrptw", "rich_alns"
    elapsed_s: float
    metrics: dict             # solver-specific telemetry; safe to log/persist as JSON


class SolverAdapter(Protocol):
    name: str

    def solve(self, problem: VRPInput, options: SolveOptions) -> SolveResult:
        ...

    def supports(self, problem: VRPInput) -> bool:
        ...

    def explain_limits(self, problem: VRPInput) -> list[str]:
        ...
```

`name` is one of the constants `"human_dispatcher"`, `"ortools_cvrptw"`, `"rich_alns"`. These are the values written to `RouteScenario.solver_kind` (see M0 schema change) and used by the registry to dispatch.

`supports()` returns False if the input requires a feature the adapter cannot handle (for example, the rich-VRP solver's M0 stub returns whatever OR-Tools supports). `explain_limits()` lists the human-readable reasons — this is what gets shown to the user when a solver is filtered out.

---

## Milestone roadmap (overview)

| ID | Milestone | Approx. effort (report) | Acceptance gate |
|---|---|---:|---|
| **M0** | Solver registry, adapter wiring, "Optimally Assign" rerouted to `RichALNSAdapter` (delegates to OR-Tools internally) | 1 person-week | All existing tests green; `assign_optimal` produces byte-identical `RouteScenario` rows as before for 5 fixed seeds; new scenario `rich_alns_service_first` appears in comparison and matches `service_first_full_fleet` |
| **M1** | Foundation: domain layer, lexicographic objective, feasibility kernel, benchmark harness, greedy seeder | 4 person-weeks | Exact small-instance parity with OR-Tools on `tests/benchmarks/` 10–30 order cases (within 5%); harness reports per-solver metrics in JSON |
| **M2** | Core ALNS engine (destroy + repair + RRT acceptance + segmented roulette wheel) | 6 person-weeks | New solver beats current OR-Tools on internal 40–100 order cases at equal time budget |
| **M3** | Local search: relocate, exchange, 2-opt, 2-opt*, cross-exchange, reload, optional-order, compatibility, candidate lists | 8 person-weeks | ≥30% solution quality gain over M2 on 100–500 order benchmarks; deterministic with fixed seed |
| **M4** | HGS hybridization: feasible/infeasible elite pools, route-based crossover (SREX-style), adaptive penalties | 6 person-weeks | Beats `service_first_full_fleet` OR-Tools on ≥80% of `vrp_benchmark_suite` instances at equal wall-clock time. **Phase B flip happens here.** |
| **M5** | Route-pool SP recombination + CP-SAT micro-repair on borders | 6 person-weeks | ≥0.5% average objective improvement over M4 on the same suite (matches the meta-analysis number cited in the report) |
| **M6** | Decomposition (clustering + rolling horizon + sparse global graph + parallel zone solves) | 6 person-weeks | Solves 2k+ order synthetic instances within budget; quality regression on small instances ≤2% |
| **M7** | Compiled evaluator (Cython or Rust+PyO3) for hot path: feasibility checks, segment cache, neighborhood evaluator | (within "compiled local search" 8 weeks) | Wall-clock speedup ≥5× on M3 local-search-dominated benchmarks; exact-result equivalence with pure Python |
| **M8** | Production hardening: telemetry, replay tests, fallback policies, rollout tooling, SLA tests, frontend solver-kind column | 4 person-weeks | P50/P95 latency targets met in staging; shadow-mode-vs-OR-Tools agreement metrics dashboarded |
| **M9** | Decommission OR-Tools from the runtime path (Phase C of D3) | 1–2 person-weeks (gated on production data) | Fallback fire rate <0.1% over rolling 30 days, zero URGENT regressions; OR-Tools removed from `RichALNSAdapter`'s runtime path; survives only as comparison scenario + bench adapter |

The remainder of this document is one section per milestone.

---

## Milestone 0 — Solver registry, adapter wiring, and shadow rollout

**Goal.** Reroute the "Optimally Assign" button through a new `RichALNSAdapter` whose internal implementation is, for now, a passthrough to the existing OR-Tools `solve()`. Add a new comparison scenario `rich_alns_service_first` that also delegates to OR-Tools `service_first_full_fleet`. After this milestone, the new solver name appears in the system without changing any user-visible behavior — the foundation for surgically swapping in the real engine in later milestones.

This milestone is the **only** one allowed to touch HTTP routes, Celery tasks, frontend code, and DB migrations until M8. Subsequent milestones modify only files inside `backend/app/algorithms/rich_vrp/` and tests. That separation is deliberate: it keeps risk in M0 and M8 (boundaries with the rest of the system) and isolates algorithm work from infrastructure work.

### [x] M0.1 — Adapter protocol and three concrete adapters

**Files to create:**

- [backend/app/algorithms/solver_adapter.py](backend/app/algorithms/solver_adapter.py) — the `SolverAdapter` protocol, `SolveOptions`, `SolveResult` dataclasses (exact code in the "Solver adapter contract" section above).
- [backend/app/algorithms/adapters/__init__.py](backend/app/algorithms/adapters/__init__.py) — empty.
- [backend/app/algorithms/adapters/human_dispatcher.py](backend/app/algorithms/adapters/human_dispatcher.py) — `HumanDispatcherBaselineAdapter`. `name = "human_dispatcher"`. `solve()` calls `solve_human_dispatcher_baseline(problem)`, wraps in `SolveResult`. `supports()` returns True always. `explain_limits()` returns `[]`.
- [backend/app/algorithms/adapters/ortools.py](backend/app/algorithms/adapters/ortools.py) — `ORToolsCVRPTWAdapter`. `name = "ortools_cvrptw"`. `solve()` calls `vrp_solver.solve(problem, initial_routes=options.initial_routes, time_limit_seconds=int(options.time_budget_s), spread_fleet=options.spread_fleet)`. `supports()` returns True always (matches today's behavior). `explain_limits()` returns `[]`.
- [backend/app/algorithms/adapters/rich_alns.py](backend/app/algorithms/adapters/rich_alns.py) — `RichALNSAdapter`. `name = "rich_alns"`. **For M0, internally instantiates `ORToolsCVRPTWAdapter` and delegates `solve()` to it.** `supports()` matches OR-Tools (returns True). `explain_limits()` returns `["delegating to OR-Tools (M0 stub)"]`. Mark with a `_DELEGATES_TO_ORTOOLS = True` class constant so future milestones can flip it off cleanly.

**Files to create:** [backend/app/algorithms/solver_registry.py](backend/app/algorithms/solver_registry.py)

```python
"""Single registry that maps solver_kind -> SolverAdapter."""
from app.algorithms.adapters.human_dispatcher import HumanDispatcherBaselineAdapter
from app.algorithms.adapters.ortools import ORToolsCVRPTWAdapter
from app.algorithms.adapters.rich_alns import RichALNSAdapter
from app.algorithms.solver_adapter import SolverAdapter

_REGISTRY: dict[str, SolverAdapter] = {
    "human_dispatcher": HumanDispatcherBaselineAdapter(),
    "ortools_cvrptw": ORToolsCVRPTWAdapter(),
    "rich_alns": RichALNSAdapter(),
}

def get_solver(kind: str) -> SolverAdapter:
    if kind not in _REGISTRY:
        raise KeyError(f"Unknown solver_kind: {kind}. Known: {sorted(_REGISTRY)}")
    return _REGISTRY[kind]

def known_solver_kinds() -> tuple[str, ...]:
    return tuple(_REGISTRY)
```

**Tests to add:**

- [backend/tests/unit/test_solver_registry.py](backend/tests/unit/test_solver_registry.py): assert all three solver kinds resolve, assert `RichALNSAdapter` and `ORToolsCVRPTWAdapter` produce byte-identical `VRPSolution` objects on a fixed 10-order fixture (because the rich adapter delegates), assert `HumanDispatcherBaselineAdapter` produces the same result as direct call to `solve_human_dispatcher_baseline()`.

### [x] M0.2 — Schema migration: `RouteScenario.solver_kind`

Currently scenario solver provenance is implicit via `key`. Add a column so it's explicit and machine-readable.

**Files to modify:**

- [backend/app/models/route_scenario.py](backend/app/models/route_scenario.py) — add `solver_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="ortools_cvrptw")` on `RouteScenario`. Default chosen so existing rows after migration are correctly tagged (they were all OR-Tools except `human_dispatcher_baseline`).

**Files to create:**

- New Alembic migration under `backend/alembic/versions/` (use the project's convention for migration filenames — read the most recent migration to see the format). The migration:
  - `op.add_column("route_scenarios", sa.Column("solver_kind", sa.String(length=40), nullable=False, server_default="ortools_cvrptw"))`
  - `op.execute("UPDATE route_scenarios SET solver_kind = 'human_dispatcher' WHERE key = 'human_dispatcher_baseline'")`
  - `op.alter_column("route_scenarios", "solver_kind", server_default=None)` to drop the server default once the backfill is done (Alembic-on-Postgres idiom).

**Files to modify:**

- [backend/app/services/route_scenario_service.py:75-106](backend/app/services/route_scenario_service.py:75) — extend `SCENARIO_DEFINITIONS` items with a `solver_kind` field. Existing entries: `human_dispatcher_baseline -> "human_dispatcher"`; all four others -> `"ortools_cvrptw"`. Add a 6th entry:

```python
{
    "key": "rich_alns_service_first",
    "label": "Rich ALNS (service-first)",
    "routing_objective": "SERVICE_FIRST",
    "fleet_utilization": "FULL_FLEET",
    "solver_kind": "rich_alns",
},
```

- The same file, `create_comparison()` at line 113: pass `solver_kind=definition["solver_kind"]` when building `RouteScenario` rows.
- [backend/app/services/route_scenario_service.py:736](backend/app/services/route_scenario_service.py:736) `_solve_scenario_vrp()`: replace the `if scenario.key == HUMAN_DISPATCHER_BASELINE_KEY` if/elif with a dispatch on `scenario.solver_kind`. The new shape:

```python
adapter = get_solver(scenario.solver_kind)
options = SolveOptions(
    time_budget_s=primary_solver_limit,
    seed=0,
    spread_fleet=(scenario.fleet_utilization == "FULL_FLEET"),
    skip_multi_trip=problem["skip_multi_trip"],
    routing_objective=scenario.routing_objective,
    fleet_utilization=scenario.fleet_utilization,
)
result = await asyncio.to_thread(adapter.solve, vrp_input, options)
```

The downstream code that consumes `solution`, `vrp_vehicles`, `multi_trip_vehicles`, `cluster_vrp_inputs`, and `solver_metrics` must be preserved. Step 1: refactor `_solve_batch_variant()` so the *prep* portion (everything that builds `vrp_orders`, `vrp_vehicles`, `cluster_vrp_inputs`, multi-trip handling) is split from the *solve* portion. Step 2: the prep stays in dispatch_service; the solve becomes the adapter call. Step 3: `_solve_human_dispatcher_baseline_variant()` is deleted in favor of going through the adapter (the human adapter calls the same underlying free function).

This refactor is the largest piece of churn in M0. Read [backend/app/services/dispatch_service.py:631-887](backend/app/services/dispatch_service.py:631) and [backend/app/services/dispatch_service.py:889](backend/app/services/dispatch_service.py:889) carefully before starting. Do it as one mechanical refactor with the existing tests passing at each step. If something in the prep portion needs to know which solver is going to run (e.g., for skip_multi_trip), pass `scenario.solver_kind` into the prep function explicitly — do not reach into the adapter for it.

### [x] M0.3 — Reroute the "Optimally Assign" button

**Files to modify:**

- [backend/app/workers/optimization_tasks.py:112](backend/app/workers/optimization_tasks.py:112) — `run_batch_optimization()`: this is the Celery task that handles "Optimally Assign". Find the call site that today invokes the OR-Tools path. Replace it with `get_solver("rich_alns").solve(vrp_input, options)`. Keep all surrounding persistence logic (`_persist_solution`, etc.) unchanged. The result of `adapter.solve(...)` exposes `.solution` of type `VRPSolution`, which is the same type the existing code already consumes — so the change is one line in the call site, not a refactor of persistence.

Behavioral check: because `RichALNSAdapter` in M0 delegates to OR-Tools, `assign-optimal` produces the same routes as before — verify with the test described in M0.5.

### [x] M0.4 — Frontend: surface the new scenario in the comparison overlay

The comparison overlay at [frontend/src/pages/OrdersPage.tsx:216](frontend/src/pages/OrdersPage.tsx:216) already iterates over scenarios returned by the API. Adding `rich_alns_service_first` to `SCENARIO_DEFINITIONS` automatically makes it appear. There is one cosmetic update:

**File to modify:** [frontend/src/pages/OrdersPage.tsx](frontend/src/pages/OrdersPage.tsx) (the `RouteScenarioComparisonOverlay` component, scenario card section around line 289–329) — add a small `solver_kind` badge next to the scenario label so the user can see which solver produced each card. Pull the value from `scenario.solver_kind` via the response schema. Style: muted, uppercase, monospace, e.g. `RICH_ALNS`. No new layout, just a `<span>` inside the existing card header.

**Files to modify:** [backend/app/schemas/route_scenario.py](backend/app/schemas/route_scenario.py) — add `solver_kind: str` to whichever `RouteScenarioRead`/`RouteScenarioResponse` model is sent to the frontend (read the file to find the right model). Add `solver_kind: string;` to the matching TypeScript type (search the frontend `src/api/types.ts` or wherever scenario types are declared).

This is the only frontend change in M0. Defer all M8 dashboards.

### [x] M0.5 — Tests for M0

The acceptance bar for M0 is "no behavioral change visible to the user." Express that as tests:

- [backend/tests/unit/test_solver_registry.py](backend/tests/unit/test_solver_registry.py) — registry resolves all three kinds, raises `KeyError` for unknown.
- [backend/tests/unit/test_solver_adapters_passthrough.py](backend/tests/unit/test_solver_adapters_passthrough.py) — for a frozen 30-order fixture and 5 fixed seeds, `RichALNSAdapter.solve(...)` returns a `VRPSolution` byte-equal to `ORToolsCVRPTWAdapter.solve(...)`. Use `dataclasses.asdict` for comparison.
- [backend/tests/integration/test_assign_optimal_via_adapter.py](backend/tests/integration/test_assign_optimal_via_adapter.py) — call `run_batch_optimization` with a small fixture and assert that the persisted `Route` rows match the rows produced by the pre-M0 (OR-Tools-direct) path. Use a recorded snapshot of the previous behavior.
- [backend/tests/integration/test_scenario_comparison_includes_rich_alns.py](backend/tests/integration/test_scenario_comparison_includes_rich_alns.py) — run a comparison; assert 6 scenarios are produced, the new `rich_alns_service_first` scenario has `solver_kind == "rich_alns"`, and its summary metrics equal `service_first_full_fleet` (because both go through OR-Tools).
- Keep the existing tests at [backend/tests/unit/test_dispatch_solver_selection.py](backend/tests/unit/test_dispatch_solver_selection.py) green by adapting them to the new dispatch path (they currently test the if/elif branching).

### M0 verification

Run from the worktree root:

```bash
cd backend && pytest tests/unit/test_solver_registry.py tests/unit/test_solver_adapters_passthrough.py -v
cd backend && pytest tests/integration/test_assign_optimal_via_adapter.py tests/integration/test_scenario_comparison_includes_rich_alns.py -v
cd backend && pytest tests/unit/test_dispatch_solver_selection.py -v
cd backend && pytest -k "vrp or solver or scenario or dispatch" -v   # smoke
cd backend && pytest --cov=app.algorithms.adapters --cov=app.algorithms.solver_registry  # coverage on new code
cd frontend && npm run test
cd frontend && npm run typecheck
cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head   # migration round-trip
```

Manual smoke test of the side-by-side comparison and "Optimally Assign" buttons in the dev frontend, on a seeded day with 30–50 orders. Visually verify:

- 6 scenarios are listed in the comparison overlay.
- Each card shows its `solver_kind` badge.
- `rich_alns_service_first` and `service_first_full_fleet` produce identical numbers (cost, distance, on-time%).
- Pressing "Optimally Assign" creates the same routes as a comparison `service_first_full_fleet` would.

If all of the above passes, M0 is done. **Stop and demo to the user.** Do not start M1 work in the same PR.

---

## Milestone 1 — Foundation: domain layer, objective evaluator, feasibility kernel, benchmark harness

**Goal.** Build the data structures, evaluator, and harness that every later milestone depends on. No actual ALNS yet — by the end of M1, `RichALNSAdapter` still delegates to OR-Tools, but the pieces underneath are in place: a canonical solver-internal model, a lexicographic objective function, a feasibility kernel (delta-cost-friendly), a benchmark runner that runs all three adapters on a suite and emits comparable metrics, and a greedy seeder that produces an initial feasible (or partially feasible) solution from `VRPInput`.

The package layout introduced in this milestone matches the layout in the research report's "Proposed module layout" section, scoped to `backend/app/algorithms/rich_vrp/`:

```
backend/app/algorithms/rich_vrp/
    __init__.py
    domain/
        __init__.py
        models.py          # solver-internal models (Solution, Route, Visit, ...)
        builders.py        # VRPInput -> internal models, internal -> VRPSolution
    objective/
        __init__.py
        cost.py            # generalized economic cost computation
        lexicographic.py   # the (unserved, lateness+ot, cost, stability) tuple
    feasibility/
        __init__.py
        kernel.py          # incremental check: time, capacity, precedence, compat
        cache.py           # per-route segment cache (will become hot path in M3)
    constructive/
        __init__.py
        greedy_seed.py     # paired insertion greedy seeder
        warm_start_seed.py # ingest existing warm_start_cache assignments
    bench/
        __init__.py
        datasets.py        # fixture loader (synthetic + Solomon/Li-Lim later)
        runner.py          # runs all adapters, collects metrics, dumps JSON
        reports.py         # human-readable diff / table output
    engine.py              # placeholder: will hold the ALNS loop in M2
```

### [x] M1.1 — Internal domain models

**Files to create:** [backend/app/algorithms/rich_vrp/domain/models.py](backend/app/algorithms/rich_vrp/domain/models.py)

Why a separate internal model: the existing `VRPInput`/`VRPSolution` shape is OR-Tools-shaped (flat node indices, `pickup_index`/`delivery_index` referring to a shared `locations` list). The new solver wants:

- Request-centric routes (a route is a list of `Request` references with their pickup/delivery slots, not a flat node sequence).
- Mutable structures with cheap inserts/deletes (the OR-Tools shape is intentionally immutable).
- A direct handle on each request's compatibility set, optionality flag, and priority class, without re-deriving from indices.

Define these dataclasses (mutable; use `slots=True` for memory):

- `Request`: `id: str`, `pickup_node: int`, `delivery_node: int`, `pickup_window: tuple[int, int]`, `delivery_window: tuple[int, int]`, `hard_delivery_deadline: int | None`, `service_time_pickup_s: int`, `service_time_delivery_s: int`, `load: dict[str, int]` (weight_g, volume_cm3, package_count), `priority: OrderPriority`, `unserved_penalty: int`, `lateness_penalty_per_min: int`, `priority_multiplier: float`, `required_tags: frozenset[str]`, `allowed_vehicle_indices: frozenset[int] | None`, `optional: bool` (`True` if dropping is allowed without infinite penalty — pulled from non-URGENT priority + finite penalty).
- `Vehicle`: `id: str`, `physical_id: str`, `trip_index: int`, `start_node: int`, `end_node: int`, `shift_window: tuple[int, int]`, `hard_shift_end: int | None`, `capacity: dict[str, int]`, `tags: frozenset[str]`, `cost: VehicleCost`, `profile: str`, `max_distance_m: int`, `max_stops: int`, `break_window: tuple[int, int] | None`, `break_duration_s: int`.
- `VehicleCost`: `fixed_route: int`, `distance_per_km: int`, `labor_per_min: int`, `fuel_per_km: int`, `overtime_per_min: int`.
- `Visit`: `request_id: str`, `kind: VisitKind` (enum: `PICKUP`, `DELIVERY`, `RELOAD`, `BREAK`, `DEPOT_START`, `DEPOT_END`), `node: int`, `arrival_s: int`, `departure_s: int`, `cumulative_load: dict[str, int]`. Mutable; rewritten by the evaluator after every move.
- `Route`: `vehicle: Vehicle`, `visits: list[Visit]`, `total_distance_m: int`, `total_duration_s: int`, `cost: CostBreakdown`, `feasible: bool`. Mutable list — moves edit `visits` in place.
- `Solution`: `routes: list[Route]`, `unserved: set[str]`, `objective: LexicographicObjective`. Top-level container.
- `MatrixView`: read-only wrapper around the OR-Tools-shaped matrices passed in via `VRPInput`. Methods `duration(i, j, profile)`, `distance(i, j, profile)`. Profile-aware: pulls from `VRPInput.duration_matrices[profile]` if set, else from `VRPInput.duration_matrix`.

These are the only domain types the rich-VRP package uses internally. The boundary with the rest of the codebase is `VRPInput` -> internal -> `VRPSolution` (see M1.2).

### [x] M1.2 — Boundary builders

**Files to create:** [backend/app/algorithms/rich_vrp/domain/builders.py](backend/app/algorithms/rich_vrp/domain/builders.py)

Two functions:

```python
def build_problem(vrp_input: VRPInput) -> Problem:
    """Convert VRPInput to internal Problem (requests, vehicles, matrix view)."""

def export_solution(internal: Solution, vrp_input: VRPInput) -> VRPSolution:
    """Convert internal Solution back to VRPSolution shape that callers expect."""
```

`Problem` is a dataclass with `requests: list[Request]`, `vehicles: list[Vehicle]`, `matrix: MatrixView`, `locations: list[tuple[float, float]]`, `service_date_anchor: int` (seconds-from-midnight 0). The export must reproduce the exact `PlannedStop`/`PlannedRoute`/`EconomicCostBreakdown` shape that downstream code consumes — copy field names from [vrp_solver.py:101-150](backend/app/algorithms/vrp_solver.py:101).

**Tests to add:** [backend/tests/unit/test_rich_vrp_domain.py](backend/tests/unit/test_rich_vrp_domain.py) — round-trip property: for fixtures used by the existing OR-Tools tests, `export_solution(build_problem(vrp_input)._fresh_empty_solution())` produces a `VRPSolution` with all orders unassigned and zero distance. Then assert that for a hand-constructed feasible solution, exporting and rebuilding preserves all stop times within ±1 second tolerance.

### [x] M1.3 — Lexicographic objective

**Files to create:** [backend/app/algorithms/rich_vrp/objective/lexicographic.py](backend/app/algorithms/rich_vrp/objective/lexicographic.py) and [backend/app/algorithms/rich_vrp/objective/cost.py](backend/app/algorithms/rich_vrp/objective/cost.py).

`cost.py` has `compute_route_cost(route, problem) -> CostBreakdown` mirroring [vrp_solver.py:1092 `_build_route_cost_breakdown`](backend/app/algorithms/vrp_solver.py:1092) (read it carefully — match the formulas exactly; then add the per-priority lateness multiplier as in [vrp_solver.py:1119](backend/app/algorithms/vrp_solver.py:1119)). The output `CostBreakdown` has the same fields as `EconomicCostBreakdown`.

`lexicographic.py` defines `LexicographicObjective`, a 4-tuple ordered as `(unserved_count_weighted, lateness_min_plus_overtime_min, generalized_cost, stability_distance)`. Implements `__lt__`, `__eq__`, `__add__` (so per-route objectives can be summed for the whole solution), and `as_tuple()` for logging. `unserved_count_weighted` weights URGENT > HIGH > NORMAL > LOW so dropping an URGENT order is always worse than dropping any number of LOW orders.

`stability_distance` defaults to 0 in M1; in M8 it becomes the edit distance vs warm-start.

**Tests to add:** [backend/tests/unit/test_lexicographic_objective.py](backend/tests/unit/test_lexicographic_objective.py) — assert ordering invariants: solution with 1 unserved URGENT > solution with 100 unserved LOW; solution with 0 unserved and 1000 lateness minutes > solution with 0 unserved, 0 lateness, 1e12 cost; etc.

### [x] M1.4 — Feasibility kernel

**Files to create:** [backend/app/algorithms/rich_vrp/feasibility/kernel.py](backend/app/algorithms/rich_vrp/feasibility/kernel.py) and [backend/app/algorithms/rich_vrp/feasibility/cache.py](backend/app/algorithms/rich_vrp/feasibility/cache.py).

Kernel responsibilities:

- `evaluate_route(route, problem) -> RouteEvaluation`: full re-evaluation. Computes arrival/departure times, cumulative load, total distance, total duration, hard window violations, capacity violations, precedence violations, compatibility violations, overtime, lateness. Returns `RouteEvaluation(feasibility: Feasibility, hard_violations: list[Violation], soft_metrics: SoftMetrics, cost: CostBreakdown)`. `Feasibility` is an enum: `FEASIBLE`, `SOFT_INFEASIBLE` (only soft windows / overtime), `HARD_INFEASIBLE`.
- `evaluate_segment(route, start_idx, end_idx, problem) -> SegmentEvaluation`: partial re-evaluation between two indices. Used by local search in M3 — but in M1 it can just call `evaluate_route` and slice; the optimization comes in M3.
- `is_compatible(request, vehicle) -> bool`: required tags ⊆ vehicle tags; allowed_vehicle_indices includes vehicle.index; vehicle is allowed for fragile/temp orders per existing flags.
- `feasible_insertion_positions(route, request, problem) -> Iterable[tuple[int, int]]`: yields valid `(pickup_idx, delivery_idx)` pairs where pickup_idx <= delivery_idx and both insertions keep capacity, time, and compatibility constraints satisfied. Time check uses time-window propagation forward from pickup_idx and backward from delivery_idx.

The cache module ([feasibility/cache.py](backend/app/algorithms/rich_vrp/feasibility/cache.py)) defines `SegmentCache` — a per-route precomputed array of forward arrival times and backward latest departures, so `evaluate_segment` is O(1) per insertion check after one O(|route|) precompute. M1 implements the precompute and the lookup; M3 wires it into the local-search inner loop. Document the structure clearly — the implementing agent in M3 needs it.

**Tests to add:** [backend/tests/unit/test_feasibility_kernel.py](backend/tests/unit/test_feasibility_kernel.py) — for several fixtures (taken from existing OR-Tools fixtures), verify that:
- a hand-built feasible solution evaluates `FEASIBLE` with violations `[]`.
- forcing a too-tight time window flips it to `HARD_INFEASIBLE`.
- exceeding capacity by 1 gram flips it to `HARD_INFEASIBLE`.
- soft-window violations are reported in `SoftMetrics.lateness_minutes`, not `hard_violations`.
- compatibility check matches [solution_validator.py](backend/app/algorithms/solution_validator.py) on the same input.

### [x] M1.5 — Greedy paired-insertion seeder

**Files to create:** [backend/app/algorithms/rich_vrp/constructive/greedy_seed.py](backend/app/algorithms/rich_vrp/constructive/greedy_seed.py)

The simplest construction that already produces feasible solutions for most instances:

1. Sort requests by `(priority, hard_deadline, latest_window)` (URGENT first).
2. For each request, in order, find the cheapest feasible insertion across all (vehicle, pickup_idx, delivery_idx) triples using `feasible_insertion_positions`. Cheapest = smallest delta in `LexicographicObjective`.
3. If no feasible insertion exists, mark the request unserved and continue.
4. Return the resulting `Solution`.

Reuse the existing [insertion_heuristic.py](backend/app/algorithms/insertion_heuristic.py) logic where applicable — but the new greedy operates on the internal `Route`/`Visit` model, not on OR-Tools index sequences, so a translation layer is needed. Function: `seed_greedy(problem: Problem, *, candidate_size: int = 24) -> Solution`. `candidate_size` is the k-nearest cap on which vehicles get evaluated per request — not a hard limit but a perf knob; default 24 matches the report's recommended starting value for "urban dense" deployments.

**Files to create:** [backend/app/algorithms/rich_vrp/constructive/warm_start_seed.py](backend/app/algorithms/rich_vrp/constructive/warm_start_seed.py)

Reads `CachedAssignment` from [warm_start_cache.py](backend/app/algorithms/warm_start_cache.py), maps `order_to_courier` and `courier_routes` into the internal `Solution` shape, then re-evaluates feasibility (because the world may have changed between cache and now). Any request that maps to a courier no longer in the problem, or that fails feasibility, is dropped from the seed and added to a "to_repair" list. Returns `(Solution, list[str])` — the seeded solution and the list of order ids that need re-insertion.

**Tests to add:** [backend/tests/unit/test_greedy_seed.py](backend/tests/unit/test_greedy_seed.py) — on a small synthetic instance, the greedy seeder produces a feasible solution serving ≥80% of orders. Compare its objective value with the OR-Tools result on the same instance — log it, but don't gate on a specific quality target yet (M1 isn't trying to beat OR-Tools).

### [x] M1.6 — Benchmark harness

**Files to create:**

- [backend/app/algorithms/rich_vrp/bench/datasets.py](backend/app/algorithms/rich_vrp/bench/datasets.py) — `load_synthetic(name) -> VRPInput`. M1 includes: `synthetic_30`, `synthetic_60`, `synthetic_120`, `synthetic_300`. Reuse fixtures from existing tests where possible. M5 will add Solomon, Gehring & Homberger, Li & Lim loaders.
- [backend/app/algorithms/rich_vrp/bench/runner.py](backend/app/algorithms/rich_vrp/bench/runner.py) — `run_suite(adapters: list[SolverAdapter], dataset_names: list[str], seeds: list[int], time_budget_s: float) -> SuiteResults`. For each (adapter, dataset, seed), call `adapter.solve(...)` with a fixed seed, collect metrics: `objective_lex_tuple`, `served_count`, `unserved_count`, `total_distance_m`, `total_duration_s`, `late_minutes`, `overtime_minutes`, `wall_clock_s`, `solver_status`. Save as JSON to `tmp/bench/{run_id}.json`.
- [backend/app/algorithms/rich_vrp/bench/reports.py](backend/app/algorithms/rich_vrp/bench/reports.py) — `format_table(results) -> str`. Markdown table grouped by dataset, columns per adapter, rows per metric.

**Tests to add:** [backend/tests/unit/test_bench_runner.py](backend/tests/unit/test_bench_runner.py) — runs the harness over a single dataset with all three adapters and asserts the JSON output has the expected shape and the markdown table renders.

### M1 verification

```bash
cd backend && pytest tests/unit/test_rich_vrp_domain.py tests/unit/test_lexicographic_objective.py tests/unit/test_feasibility_kernel.py tests/unit/test_greedy_seed.py tests/unit/test_bench_runner.py -v
cd backend && python -m app.algorithms.rich_vrp.bench.runner --dataset synthetic_30 --dataset synthetic_60 --dataset synthetic_120 --dataset synthetic_300 --adapter human_dispatcher --adapter ortools_cvrptw --seeds 0 1 2 --time-budget-s 30
cd backend && pytest -k "vrp or solver or scenario or dispatch or rich_vrp" -v
```

The bench run must:
- Produce a JSON at `tmp/bench/{ts}.json` with all (adapter × dataset × seed) cells populated.
- The console-printed markdown table shows OR-Tools and human-dispatcher columns with comparable ranges.

OR-Tools should be no slower than ~30s on the 300-order case; if it's slower, the harness has a setup bug, not a solver issue. **Do not start M2 until this passes.**

---

## Milestone 2 — Core ALNS engine

**Goal.** A working pure-Python ALNS solver that, by the end of M2, beats the OR-Tools `service_first_full_fleet` scenario on internal 40–100 order benchmarks at equal wall-clock budget. Decisions per the research report § "ALNS main loop" and § "Adaptive operator selection and acceptance".

The package additions:

```
backend/app/algorithms/rich_vrp/
    alns/
        __init__.py
        engine.py             # the main loop
        state.py              # current-solution state, scratch buffers
        operator_selection.py # segmented roulette wheel + alpha-UCB
        acceptance.py         # Record-to-Record Travel + Simulated Annealing
        destroy/
            __init__.py
            random.py
            shaw.py            # related-removal
            worst_cost.py
            route.py           # whole-route destroy
            time_window.py
            compatibility.py
        repair/
            __init__.py
            greedy_paired.py
            regret_k.py
            cost_normalized.py
            compatibility_first.py
```

### [x] M2.1 — ALNS engine skeleton

**Files to create:** [backend/app/algorithms/rich_vrp/alns/engine.py](backend/app/algorithms/rich_vrp/alns/engine.py) — top-level `class RichALNS`. Constructor takes `problem: Problem`, `seeds: list[Solution]`, `config: ALNSConfig`. Method `run(time_budget_s: float, rng: random.Random) -> Solution` runs the loop in the report's pseudocode — read it carefully and reproduce literally (research report § "ALNS main loop"). The main loop is iterative; per iteration:

1. Pick destroy operator and repair operator via the operator-selection module.
2. Apply destroy to a copy of the current state.
3. Apply repair to fill back.
4. Run a single local-search sweep (M2 stub: just call `two_opt_improve` from the existing module, on each route; M3 replaces this).
5. Apply acceptance criterion (RRT default, SA fallback per `config`).
6. Update operator weights based on whether new solution improved the best, was accepted but didn't improve, or was rejected.
7. Record routes into the route pool (M2: just append to a list with a hard cap; M5 builds the real route-pool manager).
8. Periodically (every `crossover_interval_iters`) skip in M2.
9. Stop when time budget elapsed.

`ALNSConfig` is a frozen dataclass holding all knobs: candidate_size, destroy_min/max fraction, segmented_roulette_score `[8, 4, 2, 0]` (best/improved/accepted/rejected), decay theta, segment length, alpha-UCB alpha, RRT thresholds, SA temperature schedule, route-pool cap, etc. Defaults from the report § "Recommended hyperparameters" table.

**Files to create:** [backend/app/algorithms/rich_vrp/alns/state.py](backend/app/algorithms/rich_vrp/alns/state.py) — wraps a `Solution` plus scratch buffers reused across iterations to avoid allocations. Methods to clone, mutate, and report deltas.

### [x] M2.2 — Destroy operators

For each operator below, create a module that exports `destroy(state, problem, q, rng) -> tuple[State, list[Request]]` where `q` is the number of requests to remove and the returned `list[Request]` is what was removed (so the repair knows what to put back).

- `random.py` — uniform random selection of q requests. Constant time per pick.
- `shaw.py` — Shaw's related-removal: pick a seed request randomly, then iteratively remove the request most "related" to a randomly chosen already-removed one. Relatedness = α·spatial + β·temporal + γ·load_similarity + δ·compatibility_overlap, with α=0.5, β=0.3, γ=0.1, δ=0.1 as defaults. Use `MatrixView.distance` for spatial.
- `worst_cost.py` — remove the q requests whose insertion cost (computed as the gain in route cost if they were removed) is largest. Implement with a priority queue across all routes.
- `route.py` — remove all requests on a single chosen route (or a small number of routes). Choice: route with the highest cost-per-served-order.
- `time_window.py` — remove the q requests with the most slack-violating arrival times (largest `lateness_minutes`).
- `compatibility.py` — remove requests assigned to vehicles that are barely compatible (`required_tags ⊂ vehicle.tags` with no slack), so that they can be reshuffled.

Reload-destroy and border-destroy are deferred to M5/M6 — those depend on multi-trip and decomposition respectively.

**Tests to add:** [backend/tests/unit/test_alns_destroy.py](backend/tests/unit/test_alns_destroy.py) — for each operator, a small test that asserts:
- exactly `q` requests are removed.
- the returned list contains the removed requests.
- the remaining solution is still feasible (moves only remove, never invalidate other requests).

### [x] M2.3 — Repair operators

- `greedy_paired.py` — restore each removed request via the cheapest feasible (pickup_pos, delivery_pos) on any vehicle. Uses `feasible_insertion_positions` from the feasibility kernel.
- `regret_k.py` — for k=2 or k=3, for each removed request compute the best k feasible insertion costs across all vehicles. Pick the request with the largest regret (cost difference between best and k-th best) and insert it at its best position. Repeat. The classic regret heuristic.
- `cost_normalized.py` — like greedy, but the cost is normalized by the request's `priority_multiplier` so high-priority requests are inserted first.
- `compatibility_first.py` — sort removed requests by `len(allowed_vehicle_indices)` ascending (most-restrictive first), then greedy-insert.

When a removed request can't be feasibly reinserted anywhere, it stays in `solution.unserved`. The `LexicographicObjective` already handles this — repair operators do not need to know how to give up gracefully beyond "skip this one."

**Tests to add:** [backend/tests/unit/test_alns_repair.py](backend/tests/unit/test_alns_repair.py) — for each repair, after destroy + repair on a small fixture, assert:
- no duplicates (each request appears in at most one route).
- pickup is always before delivery on its route.
- if all positions are infeasible for a request, it stays in `unserved`.
- the resulting solution is feasible (all hard windows respected).

### [x] M2.4 — Operator selection

**Files to create:** [backend/app/algorithms/rich_vrp/alns/operator_selection.py](backend/app/algorithms/rich_vrp/alns/operator_selection.py)

Two implementations:

- `SegmentedRouletteWheel`: per the research report § "Adaptive operator selection and acceptance", maintains per-operator weights, decays with theta after each segment, scores `[8, 4, 2, 0]` as default. After each segment of N iterations, recompute `w' = θ·w + (1-θ)·avg_score_in_segment / total_uses`.
- `AlphaUCB`: classical UCB1 with the bandit reward = improvement in best-so-far objective, alpha = 0.05 default. Selects the operator with the highest `mean_reward + α·sqrt(log(t)/n_i)`.

Both implement `select(rng) -> int` returning an operator index, and `update(idx, score)` after each iteration.

**Tests to add:** [backend/tests/unit/test_operator_selection.py](backend/tests/unit/test_operator_selection.py) — basic scenarios: seg-roulette never picks an operator with weight 0; seeded-RNG reproducibility; UCB explores then exploits.

### [x] M2.5 — Acceptance criteria

**Files to create:** [backend/app/algorithms/rich_vrp/alns/acceptance.py](backend/app/algorithms/rich_vrp/alns/acceptance.py)

- `RecordToRecordTravel`: accept if `cost(new) - cost(best) <= threshold`. `threshold` shrinks linearly from `start_pct * cost(initial)` to `end_pct * cost(initial)` over the run. Defaults: 1.5%, 0.15%.
- `SimulatedAnnealing`: accept with probability `exp(-(cost(new) - cost(curr)) / T)` where T cools geometrically. Default cooling rate 0.9995 per iteration.

For lexicographic objective, "cost" is the third tuple component (generalized cost). The first two components (unserved, lateness+OT) are treated as hard guards: if `new` is worse on them, reject without RRT/SA evaluation. This matches the report's lexicographic discipline.

### [x] M2.6 — Wire it into `RichALNSAdapter`

**Files to modify:** [backend/app/algorithms/adapters/rich_alns.py](backend/app/algorithms/adapters/rich_alns.py)

Replace the M0 OR-Tools delegation with:

```python
def solve(self, problem: VRPInput, options: SolveOptions) -> SolveResult:
    internal_problem = build_problem(problem)
    seeds = []
    if options.initial_routes is not None:
        seeds.append(seed_from_initial_routes(internal_problem, options.initial_routes))
    seeds.append(seed_greedy(internal_problem, candidate_size=24))
    config = ALNSConfig.from_options(options)
    engine = RichALNS(internal_problem, seeds=seeds, config=config)
    rng = random.Random(options.seed)
    final_internal = engine.run(time_budget_s=options.time_budget_s, rng=rng)
    final_external = export_solution(final_internal, problem)
    return SolveResult(
        solution=final_external,
        solver_kind="rich_alns",
        elapsed_s=engine.elapsed_s,
        metrics=engine.metrics,
    )
```

`engine.metrics` is a dict with: total_iterations, accepted_count, improving_count, best_iteration, operator_uses (per operator), operator_rewards (per operator), feasible_ratio, route_pool_size, final_lex_tuple. These are persisted into `RouteScenario.objective_breakdown` (already a JSON dict) by the existing code path — just make sure they're serializable.

**Important: Phase B is NOT triggered here.** The new solver runs only inside the `rich_alns_service_first` comparison scenario; "Optimally Assign" still goes through `RichALNSAdapter`, which still works (the new engine handles everything OR-Tools handled — but possibly worse on hard instances). To avoid production regressions in M2, add a feature flag:

**Files to modify:** [backend/app/config.py](backend/app/config.py) — add `RICH_ALNS_USE_REAL_ENGINE: bool = False`. The adapter checks this flag: if False, fall back to OR-Tools delegation; if True, run the new engine. M2 ships with the flag False in production config but True in tests so the new engine is exercised. The flag flips to True in M4 acceptance.

### M2 verification

```bash
cd backend && pytest tests/unit/test_alns_destroy.py tests/unit/test_alns_repair.py tests/unit/test_operator_selection.py tests/unit/test_acceptance.py -v
cd backend && pytest tests/unit/test_rich_alns_engine.py -v   # new file: end-to-end engine on a 30-order fixture
cd backend && RICH_ALNS_USE_REAL_ENGINE=1 python -m app.algorithms.rich_vrp.bench.runner --dataset synthetic_30 --dataset synthetic_60 --dataset synthetic_120 --adapter ortools_cvrptw --adapter rich_alns --seeds 0 1 2 3 4 --time-budget-s 60
```

Acceptance: in the bench output, on `synthetic_60` and `synthetic_120`, `rich_alns` matches or beats `ortools_cvrptw` on the lexicographic objective in ≥3 of 5 seeds. If not, debug before moving to M3.

---

## Milestone 3 — Local search and candidate lists

**Goal.** Replace the M2 "two-opt as a stub" inner loop with a real granular local search — relocate, exchange, 2-opt, 2-opt*, cross-exchange, reload, optional-order, compatibility — using candidate neighbor lists. Per the research report § "Local search neighborhoods" and § "Adaptive operator selection". Expected outcome: ≥30% objective improvement over M2 on 100–500 order benchmarks.

The package additions:

```
backend/app/algorithms/rich_vrp/
    local_search/
        __init__.py
        engine.py          # composite local search loop (round-robin or VND)
        candidate_lists.py # k-nearest neighbor structure per node
        moves/
            __init__.py
            relocate.py
            relocate_pair.py     # move both pickup and delivery
            exchange.py          # (1,1), (2,1), (2,2)
            two_opt.py           # intra-route 2-opt (reuses existing two_opt logic where possible)
            two_opt_star.py      # SwapTails — inter-route tail exchange
            cross_exchange.py
            reload_insertion.py  # M5 placeholder; implement in M5 with multi-trip
            optional_drop.py     # drop optional requests if it improves objective
            compat_repair.py     # ejection-chain for scarce vehicle classes
        evaluator.py        # incremental delta-cost evaluator
```

### [x] M3.1 — Candidate lists

**Files to create:** [backend/app/algorithms/rich_vrp/local_search/candidate_lists.py](backend/app/algorithms/rich_vrp/local_search/candidate_lists.py)

Precompute, per each node in `problem.locations`, the k nearest other nodes by `MatrixView.duration`. Store as a dict[int, list[int]] or a 2D numpy array. k = 24 (urban dense), 32 (mixed), 48 (sparse) — pick from `ALNSConfig.candidate_size`. Used by every move below to reduce neighbor exploration from O(s²) per route to O(s·k).

The list is computed once per `Problem` in the engine constructor; it's read-only and shared across all moves.

### [x] M3.2 — Move modules

Each move follows a common interface:

```python
def search(state: State, problem: Problem, candidates: CandidateLists, evaluator: Evaluator, max_moves: int = 1) -> int:
    """Try to find improving moves of this type. Apply up to max_moves. Return number of improving moves applied."""
```

The `Evaluator` is the segment-cache-backed delta evaluator from M1.4 — when a candidate move is being scored, the evaluator computes only the delta in cost from the affected segment, not the whole solution. This is the perf-critical piece.

- `relocate.py` — single-stop relocation on the same route. For each (visit, candidate position), score the delta. Apply best improving move.
- `relocate_pair.py` — relocate both pickup and delivery of a request, possibly to a different vehicle. The interesting move because of pickup-delivery precedence — must check that delivery_pos > pickup_pos in the destination route.
- `exchange.py` — `(1,1)`, `(2,1)`, `(2,2)` block exchanges between routes. Delta evaluation via segment cache.
- `two_opt.py` — intra-route 2-opt. Reuse the existing implementation in [backend/app/algorithms/two_opt.py](backend/app/algorithms/two_opt.py:36) where possible — call it directly on each route after converting to/from the internal `Visit` shape.
- `two_opt_star.py` — inter-route 2-opt* (a.k.a. SwapTails). Pick two arcs from two different routes; swap their tails. Must respect pickup-delivery precedence: if a swap separates a pickup from its delivery, reject the move.
- `cross_exchange.py` — exchange a contiguous segment of one route with a contiguous segment of another.
- `optional_drop.py` — for each optional request currently served, check if removing it (and paying its `unserved_penalty`) improves the lexicographic objective. If yes, drop it.
- `compat_repair.py` — ejection-chain style: if a high-priority request is unserved due to compatibility scarcity, try to eject a lower-priority compatible request from a vehicle, insert the high-priority one, then re-insert the ejected one elsewhere.

`reload_insertion.py` is implemented in M5 alongside multi-trip handling — leave it as a no-op stub with a docstring linking to M5.

### [x] M3.3 — Local-search engine: VND or round-robin

**Files to create:** [backend/app/algorithms/rich_vrp/local_search/engine.py](backend/app/algorithms/rich_vrp/local_search/engine.py)

Variable Neighborhood Descent (VND) ordering: cheapest neighborhoods first. Default order: `relocate -> relocate_pair -> 2-opt -> 2-opt* -> exchange -> cross_exchange -> optional_drop -> compat_repair`. After each successful improving move, restart from the first neighborhood. Stop when no neighborhood produces an improvement.

Time budget per LS sweep: a fraction of the per-iteration ALNS time (default: half). The LS engine exposes `improve(state, max_seconds: float) -> State`.

### [x] M3.4 — Wire into ALNS engine

**Files to modify:** [backend/app/algorithms/rich_vrp/alns/engine.py](backend/app/algorithms/rich_vrp/alns/engine.py) — replace the M2 stub `local_search(repaired_state)` call with the real LS engine. Pass `evaluator` and `candidates` through.

### M3 verification

```bash
cd backend && pytest tests/unit/test_local_search_moves.py -v
cd backend && pytest tests/unit/test_local_search_engine.py -v
cd backend && pytest tests/unit/test_rich_alns_engine.py -v   # extended fixture
cd backend && RICH_ALNS_USE_REAL_ENGINE=1 python -m app.algorithms.rich_vrp.bench.runner --dataset synthetic_120 --dataset synthetic_300 --dataset synthetic_500 --adapter ortools_cvrptw --adapter rich_alns --seeds 0 1 2 3 4 --time-budget-s 120
```

Acceptance: ≥30% improvement in `LexicographicObjective.generalized_cost` median over M2's bench output on the same datasets. Rich-VRP wins on ≥4 of 5 seeds for 300+ order instances. **Stop. Demo. Then M4.**

---

## Milestone 4 — HGS hybridization

**Goal.** Add HGS-style elite population mechanics on top of the ALNS engine. After M4 the engine maintains feasible/infeasible elite pools, performs route-based crossover (SREX-style), and adapts penalties to keep ~60–70% feasible offspring (per report § "Recommended hyperparameters"). M4 acceptance is the **gate for Phase B** of D3 — the production "Optimally Assign" button starts using the real engine here.

Package additions:

```
backend/app/algorithms/rich_vrp/
    hgs/
        __init__.py
        population.py    # elite pools, diversity-aware survivor selection
        diversity.py     # broken-pairs distance, per-individual diversity contribution
        crossover.py     # SREX-like route-based crossover
        penalty.py       # adaptive penalty manager
```

Implementation details follow the PyVRP reference cited in the research report (§ "Why HGS and PyVRP matter"). Sketch:

- Two pools: `P_feas` (feasible offspring), `P_infeas` (infeasible). Each capped at ~25 individuals.
- Survivor selection: when a pool exceeds cap, evict the individual whose cost+diversity-bonus rank is worst.
- Crossover: pick two elite parents, copy a subset of routes from parent 1, fill remainder by reinserting parent 2's requests.
- Adaptive penalty: scale `unserved_penalty` and `lateness_penalty` up/down so the running 100-iter feasibility ratio targets 0.65 ± 0.05.

Each module gets unit tests. The ALNS engine main loop adds a `crossover_trigger` block (every `crossover_interval_iters = 200`) that pulls two parents, runs crossover, runs LS on offspring, inserts into pools.

### M4 acceptance gate (also Phase B trigger)

Run the full benchmark suite (including the new external benchmarks added in M5 — Solomon, Gehring & Homberger, Li & Lim) at equal wall-clock time vs OR-Tools `service_first_full_fleet`. Acceptance:

- Rich-ALNS beats OR-Tools on ≥80% of suite instances by `LexicographicObjective`.
- Rich-ALNS never produces an unserved URGENT request that OR-Tools would have served.
- Rich-ALNS variance across 10 seeds is ≤5% in `generalized_cost`.

If all three pass, flip `RICH_ALNS_USE_REAL_ENGINE = True` in production config. The "Optimally Assign" button now runs the real engine. Keep OR-Tools as a fallback inside the adapter: if the engine returns `SolverStatus.NOT_FOUND` or exceeds 1.2× its time budget, the adapter retries via `ORToolsCVRPTWAdapter` and uses whichever result is better.

---

## Milestone 5 — Route-pool SP recombination & CP-SAT micro-repair

**Goal.** Add the route-pool set-partitioning recombination layer the report describes as a core component (§ "Route-pool SP recombination", § "CP/MIP repair"). After M5, periodic SP solves over accumulated routes give ≥0.5% objective gain (matches the meta-analysis figure cited in the report).

Package additions:

```
backend/app/algorithms/rich_vrp/
    recombination/
        __init__.py
        route_pool.py       # collects feasible routes from all sources, dedupes
        sp_repair.py        # set-partitioning model solved with mip / ortools.linear_solver
        cp_sat_repair.py    # CP-SAT micro-repair on a small neighborhood
        border_repair.py    # M6 only: cross-zone seam repair
```

Implementation:

- `route_pool.py` exposes `RoutePool.add(route)` (with deduplication by request-set hash + cost), `RoutePool.sample_for_sp(max_routes=2000)` (returns a diverse subset).
- `sp_repair.py` builds a SP model: variables = subset of the pool, constraints = each request covered exactly once OR pays a `unserved_penalty`, vehicle-use constraint per physical courier. Solve with `ortools.linear_solver` (CBC backend) under a strict time budget (default 30s for the SP solve itself).
- `cp_sat_repair.py` extracts a small neighborhood (40–200 requests), builds a CP-SAT model (PDPTW with the actual constraints), solves with a tight budget (30s default).

ALNS engine triggers SP every `sp_interval_iters = 1500` iterations or when `iters_since_improvement > stagnation_threshold`. CP-SAT repair triggers only when a destroy leaves a hard-infeasible remnant or after SP completion to repair seams.

Add Solomon, Gehring & Homberger, Li & Lim loaders to [bench/datasets.py](backend/app/algorithms/rich_vrp/bench/datasets.py). Verify M5 against M4 on the same suite.

---

## Milestone 6 — Decomposition, rolling horizon, sparse global graph

**Goal.** Scale the engine beyond ~2k orders by clustering, parallel zone solves, sparse global matrices, and rolling-horizon orchestration. Per the research report § "Scaling strategy for 500 to 100,000 orders".

Package additions:

```
backend/app/algorithms/rich_vrp/
    decomposition/
        __init__.py
        clustering.py       # spatial+temporal+demand+compat dissimilarity clusterer
        rolling_horizon.py  # batch / tactical / reactive horizon planner
        portals.py          # portal nodes for inter-zone connectivity
        sparse_graph.py     # k-NN sparse graph + portal edges
    matrices/
        __init__.py
        provider.py         # extends osrm_client to support sparse mode
        zone_matrix.py      # dense per-zone, sparse globally
```

This milestone is more architectural than algorithmic — the rich-VRP solver now becomes a pipeline:

1. Build sparse global graph (full directed matrix only for the depots and portal nodes).
2. Cluster with the dissimilarity metric (`α·geo + β·time + γ·demand + δ·compat + ε·depot_affinity`).
3. Solve each zone in parallel (multiprocessing, one worker per zone).
4. Aggregate routes into the global route pool.
5. Run global SP recombination over the pool.
6. Border ALNS on cross-zone seams.

Reuse existing [backend/app/algorithms/geo_clustering.py](backend/app/algorithms/geo_clustering.py) `cluster_orders()` as the starting point — extend its dissimilarity to include the temporal/demand/compat dimensions.

Rolling horizon: three modes (batch / tactical / reactive). Implemented as separate entry points on the adapter, not as a single `solve` call:

```python
class RichALNSAdapter:
    def solve(...): ...                  # batch
    def solve_tactical(...): ...         # 15–60 min replan, only un-started trips
    def solve_reactive(...): ...         # event-triggered local repair
```

Frontend changes: none in M6. The "Optimally Assign" button continues to call `solve()` (batch). M8 will add UI for tactical/reactive.

---

## Milestone 7 — Compiled evaluator

**Goal.** Move the local-search hot path (feasibility kernel, segment cache, candidate-list lookups, delta-cost evaluator) into a compiled language for production performance. Per report § "Why the primary rewrite should be ALNS-driven" and § "HGS population mechanics" — local search consumes 80–90% of runtime; this is the bottleneck.

Two viable paths; pick one:

- **Cython** — faster to implement, FFI is invisible to Python callers, builds via `setup.py`. Recommended.
- **Rust + PyO3** — better long-term, but adds a Rust toolchain to the project's dependency surface.

The plan assumes Cython. If switched to Rust, the file layout below changes to `backend/app/algorithms/rich_vrp/_evaluator_rust/` with `Cargo.toml`, but the public interface is identical.

Package additions:

```
backend/app/algorithms/rich_vrp/
    local_search/
        evaluator_cpp/        # name kept generic; "_cpp" is historical
            __init__.py       # imports .so or falls back to pure-Python
            _evaluator_core.pyx  # all three passes consolidated in one .pyx
            setup.py          # builds the .so
```

The compiled module exposes the same Python interface as [feasibility/kernel.py](backend/app/algorithms/rich_vrp/feasibility/kernel.py) and the M3 `evaluator.py` — they're now thin shims that import from the compiled module. Pure-Python fallbacks remain (used in tests for correctness verification).

CI changes: add a build step that compiles the Cython module before tests run. Add a CI matrix cell that runs the full test suite with `RICH_ALNS_USE_COMPILED_EVALUATOR=False` to verify the pure-Python fallback still passes.

Acceptance: ≥5× wall-clock speedup on a representative M3 benchmark; exact-result equivalence with pure Python (compare full suite outputs at fixed seeds).

---

## Milestone 8 — Production hardening

**Goal.** Telemetry, replay tests, fallback policies, rollout tooling, SLA tests, and the frontend dashboards needed to operate the new solver in production. Per report § "Metrics to log", § "Migration roadmap", § "Main risks and mitigations".

Items:

- **Telemetry** — every metric in the report's "Metrics to log" table is emitted by the engine and persisted into `RouteScenario.objective_breakdown` (existing JSON column) or a new `solver_telemetry` table. Pick one based on volume: if the per-solve telemetry blob exceeds ~10 KB, use a separate table; otherwise inline.
- **Replay tests** — record real production inputs (orders, vehicles, matrices, warm-start) for ~50 representative days. Add a `tests/replay/` suite that re-runs each scenario through the current solver and asserts: served-count delta ≤2%, generalized-cost delta ≤5%, no URGENT request goes from served to unserved. Run nightly.
- **Fallback policies** — codify the OR-Tools fallback inside `RichALNSAdapter`: triggers (engine timeout, NOT_FOUND, exception, unserved-URGENT detected), how the fallback result is merged with the partial new-engine result.
- **Rollout tooling** — a feature flag (`RICH_ALNS_TENANT_ALLOWLIST` env var) that enables the new engine for a subset of tenants. Combined with shadow-mode logging (run both engines; persist results from the prod one but record the shadow result for offline analysis).
- **SLA tests** — synthetic load test that runs 10 concurrent comparisons and asserts P95 wall-clock budget compliance.
- **Frontend dashboards** — add a "solver telemetry" tab to the comparison overlay showing operator usage, acceptance rate, route-pool size, SP iterations, etc. Read-only; pulls from `RouteScenario.objective_breakdown`.

Acceptance: P50/P95 latency targets met in staging on replay tests; shadow-mode agreement metrics show no URGENT regressions; ≥1 week running with `RICH_ALNS_USE_REAL_ENGINE = True` for ≥1 tenant.

---

## Milestone 9 — Decommission OR-Tools from the runtime path

**Goal.** Remove OR-Tools from every production solve path so that "Optimally Assign" never invokes it, even as a fallback. After M9, OR-Tools survives in exactly two places: (1) as a comparison-overlay scenario the dispatcher can opt into, and (2) as a benchmark adapter in `bench/runner.py` for offline quality regression. It is no longer in `RichALNSAdapter`, not in the Celery worker path, not in any `await`-able execution path triggered by user buttons.

This milestone exists because keeping a "slow, battle-tested fallback always one config flip away" sounds prudent but in practice causes drift: the fallback path's tests rot, dependencies upgrade unevenly, and one day someone discovers it broke six months ago. Decommission deliberately or pay later.

### M9 entry criteria (must all be satisfied before starting M9)

These are observed in production telemetry from M8, not contrived:

1. **Fallback fire rate < 0.1%** over a rolling 30-day window. Measured as `(solves where ORToolsCVRPTWAdapter ran inside RichALNSAdapter) / (total assign-optimal solves)`. The metric is emitted as `solver.fallback_invoked_total` from the M8 telemetry layer.
2. **Zero unserved-URGENT regressions** detected by replay tests across the same 30-day window. If even one production day would have served an URGENT order under OR-Tools but didn't under rich-ALNS, M9 does not start.
3. **Quality on fallback-triggered cases is not better with OR-Tools**. Of the <0.1% solves where the fallback fired, the OR-Tools result must not be lexicographically better than the rich-ALNS partial result on ≥95% of cases. If OR-Tools keeps "saving" the engine, that is signal that the engine has a real bug, not signal that it's time to retire OR-Tools.
4. **All shadow-mode agreement metrics from M8 stable** for ≥30 days. No drift in `served_count_delta`, `lex_objective_delta`, or `wall_clock_delta` distributions.

If any one of the four fails, M9 does not start. Report the failing metric to the user, debug the engine, return to running M8 telemetry. Do not partially decommission.

### [ ] M9.1 — Remove OR-Tools delegation from `RichALNSAdapter`

**Files to modify:**

- [backend/app/algorithms/adapters/rich_alns.py](backend/app/algorithms/adapters/rich_alns.py) — remove the `ORToolsCVRPTWAdapter` instantiation, the `_DELEGATES_TO_ORTOOLS` constant, the M0 passthrough code, and the M4 `RICH_ALNS_USE_REAL_ENGINE` feature flag check. The adapter now unconditionally runs the new engine.
- [backend/app/config.py](backend/app/config.py) — remove `RICH_ALNS_USE_REAL_ENGINE`. It served its purpose at M2–M4 and at M8 for the tenant allowlist.
- [backend/app/algorithms/adapters/rich_alns.py](backend/app/algorithms/adapters/rich_alns.py) — replace the fallback's "retry via OR-Tools" branch with a structured failure response. When the engine fails (timeout, exception, unserved-URGENT, NOT_FOUND), the adapter:
  1. Returns a partial `VRPSolution` with whatever the engine produced.
  2. Sets `solver_status` to `SolverStatus.NOT_FOUND` for genuine failures, or `SolverStatus.FEASIBLE` with `unassigned_orders` populated for partial successes.
  3. Emits a structured failure telemetry event (`solver.engine_failed_no_fallback`) with the failure reason. Operations responds via dashboards, not via silent OR-Tools rescue.

This is the philosophical core of M9: a failure should be visible and rare, not silently masked.

### [ ] M9.2 — Strip OR-Tools from the dispatch service prep-and-solve path

**Files to modify:**

- [backend/app/services/dispatch_service.py](backend/app/services/dispatch_service.py) — `_solve_batch_variant` and any helpers that exist purely to feed OR-Tools (e.g., the OR-Tools-specific `initial_routes` shape conversion, OR-Tools-specific time-limit allocation logic). Audit which helpers are still used by the rich-ALNS path; delete the rest. This may shrink the file substantially.

The OR-Tools-shaped warm-start integration in [warm_start_cache.py:122 `build_initial_routes`](backend/app/algorithms/warm_start_cache.py:122) stays — it's used by the rich-ALNS adapter via `seed_from_initial_routes` from M2.6. But the OR-Tools-direct call site is gone.

### [ ] M9.3 — Keep OR-Tools as a benchmark, not a runtime dependency

**Files to keep unchanged:**

- [backend/app/algorithms/vrp_solver.py](backend/app/algorithms/vrp_solver.py) — the OR-Tools solver source remains. It is not deleted; the user explicitly directed that the two original benchmarks must stay. After M9 it is no longer invoked from the Celery worker or from `RichALNSAdapter`, but it is still callable from:
  - `ORToolsCVRPTWAdapter` — which is still in `solver_registry.py`.
  - The `ortools_cvrptw` scenarios in `SCENARIO_DEFINITIONS` — these are still listed in the comparison overlay so dispatchers can run side-by-side comparisons on demand.
  - `bench/runner.py` — for offline quality regression.

The distinction: OR-Tools is now a **dispatcher-opt-in benchmark**, not an implicit safety net. If a dispatcher wants to verify the new engine on a specific day, they press "Run Comparison" and see all 7+ scenarios. If they don't, the production button never touches OR-Tools.

**Files to keep but mark internal:**

- The `ortools_cvrptw` entries in `SCENARIO_DEFINITIONS` are tagged with a new field `comparison_only: bool = True` (default `False` for the rich-ALNS scenarios). The frontend can use this to render the OR-Tools scenarios in a separate "benchmarks" section of the comparison overlay, visually distinct from the production-equivalent rich-ALNS scenarios. Optional UX polish; not required for M9 acceptance.

### [ ] M9.4 — Update Celery task contract

**Files to modify:**

- [backend/app/workers/optimization_tasks.py:112 `run_batch_optimization`](backend/app/workers/optimization_tasks.py:112) — confirm only one solve path remains. There should be no branching on `solver_kind` here. The task always calls `get_solver("rich_alns").solve(...)`. If the engine fails, the task records the failure on the `OptimizationRun` and returns; it does not retry with OR-Tools.

Operationally: a failed optimization run is now a **page** for the dispatcher (via existing notification channels), not a silent OR-Tools fallback. This is intentional — failures are signal, not noise to mask.

### [ ] M9.5 — Tests for M9

- [backend/tests/unit/test_rich_alns_no_fallback.py](backend/tests/unit/test_rich_alns_no_fallback.py) — assert that `RichALNSAdapter` does not import or instantiate `ORToolsCVRPTWAdapter`. Use `inspect`/`ast` to verify no transitive import. This catches accidental regressions in future PRs.
- [backend/tests/integration/test_assign_optimal_failure_path.py](backend/tests/integration/test_assign_optimal_failure_path.py) — force the engine to fail (e.g., via a fixture with infeasible-by-construction constraints), assert that `run_batch_optimization` returns a failure record and **does not** invoke OR-Tools. Use a spy on `vrp_solver.solve` to verify zero calls.
- [backend/tests/integration/test_comparison_still_runs_ortools.py](backend/tests/integration/test_comparison_still_runs_ortools.py) — verify that "Run Comparison" still includes OR-Tools scenarios after M9. The benchmark path must remain functional even though the runtime path doesn't use it.

### M9 verification

```bash
cd backend && pytest tests/unit/test_rich_alns_no_fallback.py tests/integration/test_assign_optimal_failure_path.py tests/integration/test_comparison_still_runs_ortools.py -v
cd backend && pytest -k "vrp or solver or scenario or dispatch or rich_vrp" -v
cd backend && grep -rn "vrp_solver\|ORToolsCVRPTWAdapter" app/algorithms/adapters/rich_alns.py app/workers/optimization_tasks.py
# the grep must return no matches
cd backend && python -m app.algorithms.rich_vrp.bench.runner --dataset synthetic_300 --adapter ortools_cvrptw --adapter rich_alns --seeds 0 1 2
# must still run successfully — proves the benchmark path is intact
```

**Manual check.** Spin up dev frontend. Press "Optimally Assign" — observe that no OR-Tools telemetry events are emitted. Press "Run Comparison" — observe that 7+ scenarios run including OR-Tools cards.

After M9 ships, `RichALNSAdapter` is unencumbered: no flag, no fallback, no delegation. It is the only solver behind the production button. The 80% threshold from M4 has done its job and is not in any code path; the fallback from M4–M8 has done its job and has been deleted; OR-Tools survives where you wanted it (as a benchmark) and nowhere else.

---

## Verification protocol (universal)

Every milestone's PR must pass:

1. **Unit tests**: `cd backend && pytest tests/unit/ -v --cov=app.algorithms.rich_vrp --cov-report=term-missing`. Coverage on new code ≥85%.
2. **Integration tests**: `cd backend && pytest tests/integration/ -v` — must include the milestone's specific integration tests.
3. **Lint**: `cd backend && ruff check . && black --check .`.
4. **Type check**: `cd backend && mypy app/algorithms/rich_vrp/` (add to CI if not present).
5. **Frontend build**: `cd frontend && npm run typecheck && npm run lint && npm run test && npm run build`.
6. **Migration round-trip** (only milestones with migrations: M0, possibly M8): `cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head`.
7. **Manual smoke test**: spin up dev frontend, exercise the affected button, observe the comparison overlay (every milestone).
8. **Bench delta**: run `python -m app.algorithms.rich_vrp.bench.runner` over the milestone's relevant dataset slice and compare against the previous milestone's saved JSON. Improvement gates per the milestone acceptance.

Do not collapse multiple milestones into one PR. Each milestone is its own review cycle.

---

## Risk register and mitigations

These are the top risks for this rewrite, ordered by expected impact. The implementing agent should reread this list at the start of each milestone.

| Risk | Why it matters | Mitigation |
|---|---|---|
| **Regression on production "Optimally Assign"** | Today's button works; the new path could silently produce worse routes | M0 ships with byte-identical behavior; M2 puts new engine behind a feature flag; M4 adds quantitative gate; OR-Tools remains a fallback inside `RichALNSAdapter` permanently |
| **Hidden coupling between dispatch_service prep and the OR-Tools-shape solve** | The M0 prep/solve refactor is the riskiest churn in the project | Read [dispatch_service.py:631-887](backend/app/services/dispatch_service.py:631) carefully; refactor in small steps with tests green at each step; do not rename anything in the same PR |
| **Pure-Python performance ceiling reached before M4 acceptance gate** | If M3's local search is too slow in pure Python, M4's HGS won't win the gate | Profile early in M3; if hot path is >70% in `evaluate_route`, jump M7 ahead of M5/M6 instead of pushing through |
| **Time-window propagation bugs in feasibility kernel** | Subtle bugs here invalidate every move and silently produce bad solutions | Reuse [solution_validator.py](backend/app/algorithms/solution_validator.py) as oracle in M1 tests: every test fixture goes through both validator and new kernel; mismatches fail the build |
| **Lexicographic comparison drift across modules** | If different modules use slightly different lexicographic tuples, acceptance and operator selection will fight | Single source of truth in `lexicographic.py`; every comparator goes through it; never compare cost tuples ad-hoc |
| **Frontend silently breaks on new `solver_kind` field** | Schema additions can break TypeScript builds | M0's frontend changes are minimal and must include both the schema field and the type; the frontend `npm run typecheck` is a CI gate |
| **Migration ordering surprises in CI** | Adding `solver_kind` to a row that the worker simultaneously reads can cause read-after-write race | Migration sets server_default for backfill, then drops it; service code reads `solver_kind` lazily after migration is fully applied |
| **Over-instrumentation slowing the inner loop** | Logging every iteration kills perf | Telemetry batched: per-segment summaries only, never per-iteration |
| **PyVRP feature gap ignored** | The report explicitly notes vehicle-client compatibility is enterprise-only in PyVRP | Do not add PyVRP as a runtime dependency in any milestone; it stays a benchmark reference only (research report § "Why HGS and PyVRP matter") |
| **CP-SAT repair latency explosion** | M5 CP-SAT can run away if neighborhoods are too large | Hard cap: CP-SAT neighborhoods ≤120 requests in production, ≤200 in overnight mode (matches report § "Recommended hyperparameters") |
| **Decomposition border quality collapse** | M6 zones can produce locally optimal but globally bad cuts | Overlapping clusters with route-pool SP recombination, never hard cuts (research report § "Border-repair protocol") |

---

## Open questions

Items still underspecified — the implementing agent should ask the user before assuming an answer:

- **Tenant rollout policy in M8** — gradual per-tenant or flag-flip-all? The report recommends shadow → gated → default, which matches per-tenant rollout; confirm before M8 starts.
- **Compiled evaluator language** — Cython (default in this plan) vs Rust+PyO3. Decide before M7.
- **Data retention for replay tests** — production order/courier/matrix snapshots are sensitive; clarify what's allowed in test fixtures and where to store the replay corpus before M8.
- **OR-Tools fallback budget split** — when both engines run inside `RichALNSAdapter`, what's the time split? Default proposal: 80% to rich-ALNS, 20% reserved for OR-Tools fallback if needed. Confirm before M4 production flip.

---

## Version control discipline

This rewrite spans 40+ person-weeks. Long-lived branches are not acceptable. The implementing agent commits constantly and pushes to GitHub on a tight cadence:

- **Commit at every natural boundary**, not at milestone end. A natural boundary is: a passing test added, a single module written, a refactor step completed, a function extracted. The commit log should read like a coherent narrative of the work, not "M2 done" three weeks later.
- **One milestone is one PR**, but that PR has many commits. Do not squash-merge milestones into a single commit; the granular history is the audit trail that makes any later debugging tractable.
- **Each commit must leave the tree in a green state** — tests passing, lint clean, type-check clean. If a commit can't pass tests, mark it WIP locally and fix-forward; never push a red commit to the milestone branch.
- **Push at least daily**, more often if the work is exploratory. Pushed branches are recoverable; local-only work is not.
- **Branch naming**: `claude/rich-vrp-m{milestone}-{short-slug}`, e.g., `claude/rich-vrp-m0-adapter-wiring`, `claude/rich-vrp-m2-alns-engine`. Keeps the milestone explicit in CI output and PR titles.

**Critical: commit attribution.** Commits and PRs for this project must not advertise that Claude or any AI tool produced them. Specifically:

- **Do NOT add a `Co-Authored-By: Claude ...` line** to commit messages. This is the default boilerplate Claude Code emits; it must be omitted for every commit on this project.
- **Do NOT add a `🤖 Generated with [Claude Code]` line** or any equivalent attribution to commit bodies, PR descriptions, or PR titles.
- **Do NOT mention Claude, Anthropic, or any AI assistance** in commit messages, PR descriptions, code comments, or documentation generated by this work.
- **Authorship**: commits are authored by the user (`Doruk Yalçın`). The git config is already set up; do not modify it.
- **PR descriptions**: write them as the user would — "Adds the solver adapter protocol and reroutes assign-optimal through it." No machine attribution.

This is a hard rule. The user has stated it explicitly. Treat it the same as "do not delete the existing benchmark solvers" — non-negotiable.

---

## Final pointers for the implementing agent

- Read [deep-research-report.md](deep-research-report.md) once front-to-back before starting M0. Do not skim. Every algorithmic decision in this plan traces back to a specific section of that report.
- Do not delete or modify [backend/app/algorithms/vrp_solver.py](backend/app/algorithms/vrp_solver.py) or [backend/app/algorithms/human_dispatcher_baseline.py](backend/app/algorithms/human_dispatcher_baseline.py) at any point. They are benchmarks; their behavior must be preserved.
- Do not change the existing 5 comparison scenarios in [backend/app/services/route_scenario_service.py:75](backend/app/services/route_scenario_service.py:75). New scenarios are added; existing ones are untouched.
- Keep all rich-VRP code under [backend/app/algorithms/rich_vrp/](backend/app/algorithms/rich_vrp/). The only files outside that directory you should modify are the ones explicitly listed per milestone (mostly in M0).
- Each milestone is a separate PR. Each PR is a separate user demo. Do not advance until the user signs off.
