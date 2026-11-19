# Backup and Restore Runbook

## Scope
- Authoritative transactional state: PostgreSQL.
- Durable customer-facing files: S3 POD assets and static buckets.
- Rebuildable runtime state: Redis cache, Redis-backed rate limits, token revocation state, and Celery broker/result traffic.

## Recovery targets
- `local`: target `RPO <= 24h`, `RTO <= 4h`.
- `staging`: target `RPO <= 24h`, `RTO <= 4h`.
- `production`: initial target `RPO <= 1h`, `RTO <= 4h`.

These are intentionally conservative until cloud restore drills are run in the target environment.

## Current controls
- AWS production/staging Terraform enables RDS automated backups via `backup_retention_period` in [infra/terraform/aws/modules/data/main.tf](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/terraform/aws/modules/data/main.tf).
- Production defaults keep `db_skip_final_snapshot = false` in [infra/terraform/aws/environments/production.tfvars.example](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/terraform/aws/environments/production.tfvars.example).
- S3 versioning is enabled for dashboard, tracking, and POD buckets in [infra/terraform/aws/modules/storage/main.tf](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/terraform/aws/modules/storage/main.tf).
- Terraform bootstrap state locking uses DynamoDB point-in-time recovery in [infra/terraform/bootstrap/main.tf](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/terraform/bootstrap/main.tf).

## Local rehearsal procedure
Use this to prove that a fresh PostgreSQL database can be rebuilt from a backup artifact.

1. Ensure `DATABASE_URL` points at the source database.
2. Run:
   ```bash
   python3 infra/scripts/rehearse_postgres_restore.py \
     --database-url "$DATABASE_URL" \
     --maintenance-url "${MAINTENANCE_DATABASE_URL:-postgresql://postgres@localhost:5432/postgres}" \
     --evidence-path docs/operations/evidence/backup_restore/YYYY-MM-DD_local_restore_rehearsal.md
   ```
3. Review the generated evidence file.
4. Confirm:
   - the rehearsal completed with `PASS`
   - the Alembic version matches the source database
   - all public tables were restored with matching row counts
5. Commit the evidence file or attach it to the release/change record.

## AWS restore procedure
Use this when the runtime stack is deployed to AWS.

### PostgreSQL (RDS)
1. Identify the target restore point and the expected `RPO`.
2. Restore the RDS instance to a new instance or point-in-time clone.
3. Re-point application `DATABASE_URL` to the restored instance in the recovery environment.
4. Run:
   - `cd backend && alembic current`
   - `cd backend && python scripts/schema_inspection_checks.py`
5. Validate critical row counts against the last known healthy baseline.
6. Promote the restored instance only after application smoke checks pass.

### POD assets / static buckets
1. Locate the object version in S3 version history.
2. Restore the prior object version or copy it to a new key.
3. For broad incidents, recover bucket contents from version history before reopening upload traffic.

### Redis
Redis is treated as rebuildable, not as the source of record.

After a PostgreSQL restore:
- restart API and worker services against clean Redis
- force a new login if token revocation history was lost
- re-run scheduled jobs and manual webhook replays as needed
- expect transient queue/rate-limit data to be lost

## Evidence requirements
- Keep the latest successful rehearsal record in [docs/operations/evidence/backup_restore](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/backup_restore).
- Run [infra/scripts/validate_backup_controls.sh](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/scripts/validate_backup_controls.sh) whenever Terraform backup posture changes.
- Treat backup/restore drift as a release blocker.
