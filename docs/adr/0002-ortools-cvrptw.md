# ADR 0002: OR-Tools CVRPTW
- Status: accepted
- Context: Need capacity + pickup/delivery + time windows + solver quality under cost constraints.
- Decision: Use OR-Tools CVRPTW for batch optimization; insertion heuristic for on-demand.
- Consequences:
  - Strong solution quality with mature solver behavior.
  - Deterministic regression coverage now lives in `backend/tests/unit/test_vrp_benchmark_regressions.py`.
  - Manual benchmark snapshots can be produced with `backend/scripts/run_vrp_benchmarks.py`, which now exits non-zero on regression.
  - Benchmark coverage includes elapsed-time budgets for medium and larger clustered synthetic city batches, not just small correctness cases.
  - Timeout tuning still needs to be refined against tenant-specific production traffic, but the repo now has explicit certification thresholds for representative clustered workloads.
