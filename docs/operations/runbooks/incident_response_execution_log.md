# Incident Response Execution Log

Use one row per production, staging, or tabletop incident.

| date_utc | environment | severity | incident_type | detection_time_utc | mitigated_time_utc | resolved_time_utc | incident_commander | communications_owner | customer_impact | result | follow_up_refs | evidence_refs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-03-27 | tabletop | SEV-2 | webhook_dead_letter_alarm | 2026-03-27T05:10:29Z | 2026-03-27T05:18:00Z | 2026-03-27T05:24:00Z | codex | codex | none-tabletop | tabletop_complete | run live staging webhook dead-letter drill after deployment target is chosen | [2026-03-27_webhook_dead_letter_tabletop.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/incidents/2026-03-27_webhook_dead_letter_tabletop.md) |

## Rules
- Record actual timestamps, not estimates.
- Link every serious incident to the evidence directory and follow-up work.
- If a secret rotation or recovery procedure was triggered, include the matching execution log entry in `evidence_refs`.
