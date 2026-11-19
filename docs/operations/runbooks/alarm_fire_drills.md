# Alarm Fire Drills

## Scope
Validate CloudWatch alarm wiring and on-call response for:
- ALB unhealthy targets
- Worker ECS CPU/memory alarms
- RDS connections alarm
- Redis evictions alarm

## Preconditions
1. Staging environment deployed.
2. `alarm_topic_arns` configured to notify on-call channel/email.
3. Access to CloudWatch, ECS, RDS, and ElastiCache metrics.
4. Required alarms validated with `infra/scripts/validate_required_alarms.sh`.

## Drill 1: ALB Unhealthy Targets
1. Temporarily fail health checks by deploying API container with broken `/health`.
2. Confirm alarm `*-alb-unhealthy-targets` enters `ALARM`.
3. Confirm alert delivery to SNS subscribers.
4. Roll back deployment and confirm alarm returns to `OK`.

## Drill 2: Worker CPU/Memory
1. Run synthetic worker load (large optimization batches) for 15+ minutes.
2. Confirm `*-ecs-worker-cpu-high` and/or `*-ecs-worker-memory-high` alarms.
3. Verify alert delivery and response notes.
4. Scale workers or stop load; confirm alarm recovery.

## Drill 3: RDS Connections
1. Run connection stress script in staging.
2. Confirm `*-rds-connections-high` alarm state transition.
3. Validate alert delivery and escalation.
4. Stop stress and verify recovery.

## Drill 4: Redis Evictions
1. Temporarily constrain Redis memory in staging test profile.
2. Generate cache pressure until evictions occur.
3. Confirm `*-redis-evictions-high` alarm.
4. Restore memory profile and confirm alarm recovery.

## Evidence Capture
- Timestamped screenshots of alarm transitions.
- SNS notification samples.
- Incident notes with responder and resolution time.
- Required alarms validation output JSON from `validate_required_alarms.sh`.
- Follow-up ticket links for any false positives or missing notifications.

## Execution Log
- Record each drill run in [alarm_fire_drills_execution_log.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/alarm_fire_drills_execution_log.md).
- Mirror pass results in [section11 ledger.csv](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/section11/ledger.csv).
