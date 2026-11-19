# Disaster Recovery Execution Log

Use one row per drill, tabletop, or real recovery event.

| date_utc | environment | scenario | target_rto | actual_rto | target_rpo | actual_rpo | result | owner | notes | evidence_refs |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-03-27 | tabletop | redis_loss_worker_replay | 30m | 20m-tabletop | runtime-state-only | postgres-intact-redis-rebuild | tabletop_complete | codex | real broker replay still needs staging evidence; PostgreSQL remains source of truth | [2026-03-27_redis_loss_worker_replay_tabletop.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/disaster_recovery/2026-03-27_redis_loss_worker_replay_tabletop.md) |

## Required scenarios per quarter
- PostgreSQL logical restore rehearsal
- Redis loss / rebuild tabletop
- Worker replay tabletop
- POD object version restore rehearsal
- Regional disaster tabletop

## Rules
- Record actual impact and resolution time, not estimates.
- If a scenario misses target RTO/RPO, open a follow-up remediation item.
- Link evidence files and incident notes directly in `evidence_refs`.
