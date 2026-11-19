# Disaster Recovery Runbook

## Current posture
Current posture: single-region, warm-standby-by-redeployment, not active-active.

Regional disaster failover is not currently supported.

This means the system is designed to recover from component failure and backup restore events inside the primary region, but not from a full-region loss with automatic customer traffic failover.

## Recovery targets
- API / worker service recovery target: RTO <= 30m.
- PostgreSQL logical restore target: RPO <= 1h, RTO <= 4h.
- POD object version restore target: RPO <= 1h, RTO <= 4h.
- Redis rebuild target: RTO <= 1h.
- Regional disaster target: no committed RTO/RPO yet; customer-impacting outage expected.

These are current operational targets, not contractual guarantees.

## Failure classes

### API task or ALB target failure
- Expected behavior: ECS/ALB replace unhealthy tasks and restore service.
- Detection: ALB unhealthy target alarm, ECS CPU/memory alarms, `/health` failure.
- Recovery: redeploy or roll back through CodeDeploy; confirm `/health`, auth, order list, and tracking endpoints.

### Worker failure
- Expected behavior: ECS replaces worker task; queue processing resumes after restart.
- Detection: worker CPU/memory alarms, queue backlog, webhook dead-letter growth.
- Recovery: restart or redeploy worker service; replay webhook dead-letter items if needed.

### PostgreSQL impairment or data loss
- Expected behavior: production uses Multi-AZ RDS in the current Terraform posture.
- Detection: RDS alarms, application DB errors, migration failures, failed writes.
- Recovery: if failover does not self-heal, restore from backup or point-in-time clone and validate with the backup/restore runbook.

### Redis loss
- Redis is single-node in the current production defaults.
- Redis is treated as rebuildable runtime state, not as the source of record.
- Detection: Redis memory/eviction alarms, API cache failures, worker broker failures.
- Recovery: recreate or restart Redis, then restart API/worker services.
- Impact: cache loss, queue loss, revoked-token history loss, and temporary rate-limit state loss are currently accepted.

### POD asset loss or overwrite
- Expected behavior: S3 versioning protects object history for POD and static buckets.
- Detection: failed object fetches, operator report, bucket/object audit.
- Recovery: restore prior S3 object version and validate affected order records.

### OSRM outage
- Expected behavior: routing and optimization fail loudly; no automatic haversine fallback exists.
- Detection: OSRM health checks, dispatch failures, OSRM error counters.
- Recovery: restart or redeploy OSRM backing service, verify matrix/route health, then retry optimization jobs.

### Regional disaster
- Expected behavior: no automatic regional failover exists today.
- Recovery: restore data and redeploy services in a new region through a controlled disaster procedure.
- Current stance: tabletop/documented only; not yet rehearsed as an executed drill.

## Infrastructure posture assumptions
- Production compute runs multiple API and worker tasks.
- Production PostgreSQL is Multi-AZ and retains backups.
- Production database destroy path keeps the final snapshot.
- S3 versioning is enabled for dashboard, tracking, and POD buckets.
- Redis failover is manual in the current production defaults because `redis_num_cache_clusters = 1`.

Validate these assumptions with [infra/scripts/validate_recovery_posture.sh](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/scripts/validate_recovery_posture.sh).

## Recovery order
1. Confirm incident class and blast radius.
2. Preserve logs/metrics/evidence before destructive recovery actions.
3. Restore authoritative data plane first: PostgreSQL, then POD objects if needed.
4. Rebuild runtime dependencies next: Redis, workers, webhook replay.
5. Restore API ingress and verify `/health`, auth, orders, tracking, and dispatch smoke checks.
6. Record actual RTO/RPO and follow-up gaps in the execution log.

## Minimum smoke checks after recovery
- `/health` returns healthy.
- Auth login + refresh succeed.
- Order list and order detail load.
- Tracking token lookup succeeds.
- One webhook replay succeeds.
- One optimization preview or on-demand insertion succeeds if OSRM is available.

## Drill cadence
- Monthly: PostgreSQL logical restore rehearsal in a non-production environment.
- Monthly: alarm fire drills.
- Quarterly: Redis loss tabletop and worker replay tabletop.
- Quarterly: regional disaster tabletop.

## Evidence
- Recovery drill logs belong in [docs/operations/runbooks/disaster_recovery_execution_log.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/disaster_recovery_execution_log.md).
- Backup restore evidence belongs in [docs/operations/evidence/backup_restore](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/backup_restore).
