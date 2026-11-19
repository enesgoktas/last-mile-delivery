# Secret Rotation Execution Log

Use one row per executed rotation event.

| date_utc | environment | rotation_scope | old_key_id | new_key_id | operator | reviewer | result | evidence_refs | notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-03-27 | local-rehearsal | webhook_secret_encryption | tabletop-old-seed | tabletop-new-seed | codex | codex | blocked_schema_mismatch | [2026-03-27_local_webhook_rotation_rehearsal.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/secret_rotation/2026-03-27_local_webhook_rotation_rehearsal.md) | local `delivery_db` is still on Alembic version `20260314_0015`; rerun after migrations |

## Scope examples
- `auth_jwt`
- `tracking_jwt`
- `webhook_secret_encryption`
- `oidc_secret_encryption`
- `incident_response_bulk_rotation`

## Rules
- Always record the reason for rotation in `notes`.
- Link dry-run/apply output and verification output in `evidence_refs`.
- If the rotation was incident-driven, cross-link the incident execution log entry.
