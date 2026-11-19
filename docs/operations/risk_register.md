# Risk Register

| risk | impact | likelihood | mitigation | owner |
|---|---|---|---|---|
| VRP quality regressions | high | medium | deterministic benchmark suite in `backend/tests/unit/test_vrp_benchmark_regressions.py` with elapsed-time budgets for clustered medium/large batches + manual benchmark snapshots via `backend/scripts/run_vrp_benchmarks.py` | backend |
| Geocoding failures in imports | medium | high | preview validation + partial import + error export | backend |
| Webhook delivery failures | high | medium | retry with backoff + dead-letter logs + replay tooling | backend |
| Offline sync conflicts | high | medium | ordered queue replay + conflict responses + courier UX prompts | mobile |
| OSRM outage | high | low/medium | alerts + failover/degraded-mode docs + deterministic fault tests in `backend/tests/unit/test_dispatch_fault_paths.py`; no automatic haversine fallback is implemented in the current runtime | platform/backend |
| Backup/restore drift | high | medium | restore rehearsal evidence in `docs/operations/evidence/backup_restore` + `infra/scripts/validate_backup_controls.sh` | platform |
| Single-region outage | high | low/medium | documented DR posture + quarterly regional-disaster tabletop + explicit non-support statement until multi-region exists | platform |
