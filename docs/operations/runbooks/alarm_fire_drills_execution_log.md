# Alarm Fire Drills Execution Log

Use one row per executed drill scenario.

| date_utc | environment | alarm_name | scenario | state_transition | notification_verified | responder | notes | evidence_refs |
|---|---|---|---|---|---|---|---|---|
| 2026-03-27 | tabletop | redis-evictions-high | redis loss / rebuild tabletop | tabletop-reviewed | no-live-target | codex | live SNS or pager delivery still needs staging validation | [2026-03-27_redis_loss_worker_replay_tabletop.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/disaster_recovery/2026-03-27_redis_loss_worker_replay_tabletop.md) |

## Required Scenarios per Drill Cycle
- ALB unhealthy targets
- Worker CPU/memory
- RDS connections
- Redis evictions

## Follow-up
- Update Section 11 ledger with `11.3` pass/fail entries for staging and production after completing a full drill cycle.
