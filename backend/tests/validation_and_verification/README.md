# Verification & Validation suite

**Last updated:** 2026-05-07 (after Tier 1 completion — formal model spec + V&V chapter)

This directory contains tests that go beyond ordinary software unit tests:
they verify that the VRP system, *as a deliverable*, is correct (verification)
and that it solves the *right problem* (validation). The test files are
designed to be re-run as a coherent suite — every push, every solver change,
every release.

For the full V&V plan and the activity matrix this suite implements, see
[/Users/dorukyalcin/.claude/plans/how-do-we-add-frolicking-pnueli.md](../../../../.claude/plans/how-do-we-add-frolicking-pnueli.md).

## What V&V means

- **Verification** — *Did we build the system right?* Implementation matches
  spec; equations are encoded correctly; solutions satisfy hard constraints;
  the Cython hot-path is numerically equivalent to the Python reference.
- **Validation** — *Did we build the right system?* The system solves the
  real-world problem; performs competitively against literature benchmarks;
  responds sensibly to parameter changes; beats the operational baseline.
- **Conceptual validation** — *Did we model the right problem?* The
  mathematical model captures reality; assumptions are documented and
  defensible.

## How to run

```bash
# Run the full V&V suite (everything in this directory):
cd backend && pytest tests/validation_and_verification/ -v --no-cov

# Run just the fast checks (boundary + determinism):
pytest tests/validation_and_verification/test_boundary_conditions.py \
       tests/validation_and_verification/test_determinism.py -v --no-cov

# Run the always-on feasibility CI gate (slowest, ~2.5 min):
pytest tests/validation_and_verification/test_feasibility_gate.py -v --no-cov

# Sensitivity sweep (CLI tool — outputs CSV + PNG to .v_and_v/):
python scripts/sensitivity_sweep.py \
    --param fleet_size --range 2,3,4,5,6 --dataset synthetic_60

# Solomon VRPTW benchmark — vendor instances first, then run:
python scripts/download_solomon.py            # fetch all 56 instances
pytest tests/validation_and_verification/test_solomon.py -v --no-cov
# Outputs: tests/validation_and_verification/_results/solomon_results.csv
```

## Activity matrix — current statuses

Status legend:
- ✅ DONE — implemented and passing in this suite
- 🟡 PARTIAL — partial coverage; gaps documented inline
- 🔵 PLANNED — in the V&V plan, not yet implemented
- 📦 ELSEWHERE — exists in the codebase but outside this directory; pointer given
- ⛔ OUT OF SCOPE — explicitly deferred (see plan rationale)

### A. Verification activities

| ID | Activity | Status | Where |
| --- | --- | --- | --- |
| A1 | Independent feasibility validator (8 hard-constraint classes) | 📦 ELSEWHERE | [app/algorithms/solution_validator.py](../../app/algorithms/solution_validator.py) — runs in production, surfaces `validation_error_count` |
| A2 | Algorithmic unit-test coverage (kernel, ALNS, HGS, repair, local search) | 📦 ELSEWHERE | 113 unit tests under [tests/unit/](../unit/) |
| A3 | Integration / dispatch-flow tests | 📦 ELSEWHERE | ~29 integration tests under [tests/integration/](../integration/) |
| A4 | Cython hot-path numerically equivalent to Python reference | 📦 ELSEWHERE | [tests/unit/test_compiled_evaluator.py](../unit/test_compiled_evaluator.py) |
| A5 | Replay regression vs baseline snapshots | 📦 ELSEWHERE | [tests/replay/test_replay_regression.py](../replay/test_replay_regression.py) |
| A6 | Synthetic benchmark regression suite (4 sizes, fixed seeds) | 📦 ELSEWHERE | [tests/benchmarks/vrp_benchmark_suite.py](../benchmarks/vrp_benchmark_suite.py) |
| A7 | **Determinism audit — same seed → same served set across all adapters** | ✅ DONE | [test_determinism.py](test_determinism.py) — covers rich_alns, ortools_cvrptw, human_dispatcher × 3 seeds |
| A8 | **Boundary / edge-case suite** (empty, 1-order, 1-vehicle, same-location, capacity-too-small, infeasible TW) | ✅ DONE | [test_boundary_conditions.py](test_boundary_conditions.py) — parametrised across all 3 adapters |
| A9 | **Numerical / unit consistency check** (m vs km, s vs min, g vs kg, TL × multiplier paths) | 🔵 PLANNED | Tier 2 of plan — `docs/v_and_v/unit_consistency_audit.md` |
| A10 | **Algorithm convergence trace** (objective vs iteration) | 🔵 PLANNED | Tier 2 — `scripts/convergence_trace.py` |
| A11 | **Optimality gap on small instances** (5/8-customer to provable optimum) | 🔵 PLANNED | Tier 2 — `scripts/tiny_optimality_gap.py` |
| A12 | **Scalability / stress** (≥1 000 orders) | 🔵 PLANNED | Tier 3 — `scripts/stress_test.py` |
| A13 | **Cross-solver agreement** (rich_alns vs ortools, same input) | 🟡 PARTIAL | Compare Routes (single day in UI); systematic version planned |

### B. Validation activities

| ID | Activity | Status | Where |
| --- | --- | --- | --- |
| B1 | Same-day multi-solver comparison (UI) | 📦 ELSEWHERE | Compare Routes, [route_scenario_service.py](../../app/services/route_scenario_service.py) |
| B2 | Operational baseline (sweep heuristic representing manual dispatch) | 📦 ELSEWHERE | [human_dispatcher_baseline.py](../../app/algorithms/human_dispatcher_baseline.py) |
| B3 | **Standard literature benchmarks (Solomon VRPTW, 56 instances, gap-to-BKS)** | ✅ DONE | [test_solomon.py](test_solomon.py) — parser, runner, BKS table; vendor data via [scripts/download_solomon.py](../../scripts/download_solomon.py); writes `_results/solomon_results.csv` |
| B4 | **Sensitivity analysis** (sweep fleet size, fuel price, time-window width) | ✅ DONE | [scripts/sensitivity_sweep.py](../../scripts/sensitivity_sweep.py) — CLI tool, outputs CSV + PNG |
| B5 | **Multi-day statistical comparison vs baseline** (paired t-test, N≥30 days) | 🔵 PLANNED | Tier 2 — `scripts/backtest.py` |
| B6 | Multi-baseline comparison (vs NN, random feasible, OR-Tools, human) | 🟡 PARTIAL | Compare Routes covers human + OR-Tools; NN + random planned (Tier 2) |
| B7 | **Real-world fidelity spot-check** (OSRM vs reference) | 🔵 PLANNED | Tier 3 — `scripts/osrm_fidelity_check.py` |
| B8 | **Robustness / failure-mode tests** (OSRM down, courier no-show, late URGENT) | 🔵 PLANNED | Tier 3 — `test_failure_modes.py` |
| B9 | **UAT / domain-expert review** | 🔵 PLANNED | Tier 3 — `docs/v_and_v/uat_session.md` |
| B10 | **Comparison vs published algorithm performance** (Vidal et al. 2013, Helsgaun 2017) | ✅ DONE | Bundled with B3 — [_solomon_bks.py](_solomon_bks.py) holds Vidal/SINTEF BKS table; per-instance gap recorded in CSV |
| B11 | **Always-on feasibility audit in CI** (`validation_error_count == 0`) | ✅ DONE | [test_feasibility_gate.py](test_feasibility_gate.py) — 3 adapters × 3 instance sizes; ~2.5 min |

### C. Conceptual model validation

| ID | Activity | Status | Where |
| --- | --- | --- | --- |
| C1 | Formal problem statement (decision vars, objective, constraints, parameters) | ✅ DONE | [docs/optimization_model.md](../../../docs/optimization_model.md) — full PDPTW-MT formulation with code mapping (§9) |
| C2 | Model assumptions register | ✅ DONE | [docs/optimization_model.md §7](../../../docs/optimization_model.md) — 12 named modelling assumptions (MA1–MA12) with "when it breaks" |
| C3 | Input data sanity / quality audit | 🔵 PLANNED | Tier 2 |
| C4 | Requirements traceability matrix | 🔵 PLANNED | Tier 3 — `docs/v_and_v/requirements_traceability.csv` |
| C5 | Face validity (domain-expert review of model) | 🔵 PLANNED | Tier 3 (paired with B9) |

### D. Process / reporting artifacts

| ID | Activity | Status | Where |
| --- | --- | --- | --- |
| D1 | V&V chapter / report (single document tying all activities together) | ✅ DONE | [docs/verification_and_validation.md](../../../docs/verification_and_validation.md) — sections 1–10, with reproducibility recipe |
| D2 | Risk register | 🔵 PLANNED | Tier 3 — `docs/v_and_v/risk_register.md` |
| D3 | Decision log | 🟡 PARTIAL | [VRP_REWRITE_PLAN.md](../../../VRP_REWRITE_PLAN.md) covers some; full log Tier 3 |

## Score so far

| Category | DONE | PARTIAL | PLANNED | ELSEWHERE |
| --- | --- | --- | --- | --- |
| A. Verification | 2 | 2 | 4 | 5 |
| B. Validation | 4 | 1 | 4 | 2 |
| C. Conceptual | 2 | 0 | 3 | 0 |
| D. Process | 1 | 1 | 1 | 0 |
| **Total** | **9** | **4** | **12** | **7** |

**Tier 1 of the V&V plan is complete:** Solomon benchmark, sensitivity
sweep, determinism + boundary suites, feasibility CI gate, formal model
spec, and V&V chapter are all in place. Tier 2 (multi-day backtest +
statistical comparison, multi-baseline comparison, convergence trace)
and Tier 3 (stress, OSRM fidelity, FMEA, UAT, RTM, risk register) are
still planned.

The DONE column above is what this directory currently delivers. ELSEWHERE
items are real V&V coverage that already exists in the codebase but lives
outside this directory; PLANNED items are written up in the plan but not yet
implemented. PARTIAL items have meaningful coverage with documented gaps.

## When to update this document

Update this file whenever:
- A new test is added to this directory (move its row from PLANNED to DONE).
- A planned V&V activity is dropped or moved between tiers.
- An ELSEWHERE pointer is moved into this directory.
- The V&V plan ([how-do-we-add-frolicking-pnueli.md](../../../../.claude/plans/how-do-we-add-frolicking-pnueli.md)) changes shape.

When you update, refresh the **Last updated** date at the top.
