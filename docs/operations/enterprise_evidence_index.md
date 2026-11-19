# Enterprise Operations Evidence Index

This document is the buyer-facing map of operational controls, where they are documented, and where evidence is stored.

## Current Posture Summary
- Deployment controls: documented and CI-enforced.
- Backup / restore: documented and locally rehearsed with captured evidence.
- Disaster recovery: documented posture with explicit current limits; single-region only.
- Monitoring / alarms: documented with validation scripts and drill logs.
- Incident response: documented process and execution log template.
- Secret rotation: documented playbook for JWT, tracking, encryption, and provider credentials.
- Deployment approvals: documented two-person promotion and rollback governance.
- Security governance: documented vulnerability SLA, access review cadence, and break-glass rules.

## Control Map

| control area | primary doc | evidence location | automated guard |
|---|---|---|---|
| release / deployment controls | [ci_cd_pipeline.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/ci_cd_pipeline.md), [release_readiness.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/release_readiness.md) | [section11/ledger.csv](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/section11/ledger.csv) | `infra/scripts/check_section11_evidence.sh` |
| backup / restore | [backup_restore.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/backup_restore.md) | [evidence/backup_restore](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/backup_restore) | `infra/scripts/validate_backup_controls.sh` |
| disaster recovery posture | [disaster_recovery.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/disaster_recovery.md) | [evidence/disaster_recovery](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/disaster_recovery) | `infra/scripts/validate_recovery_posture.sh` |
| monitoring / alert drills | [monitoring_observability.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/monitoring_observability.md), [alarm_fire_drills.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/alarm_fire_drills.md) | [alarm_fire_drills_execution_log.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/alarm_fire_drills_execution_log.md) | `infra/scripts/validate_required_alarms.sh` |
| deployment approval governance | [deployment_governance.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/deployment_governance.md), [ci_cd_pipeline.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/ci_cd_pipeline.md) | [evidence/deployment_approvals](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/deployment_approvals) | `infra/scripts/validate_enterprise_ops_evidence.sh` |
| incident response | [incident_response.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/incident_response.md) | [incident_response_execution_log.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/incident_response_execution_log.md), [evidence/incidents](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/incidents) | `infra/scripts/validate_enterprise_ops_evidence.sh` |
| secret rotation | [secret_rotation.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/secret_rotation.md) | [secret_rotation_execution_log.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/secret_rotation_execution_log.md), [evidence/secret_rotation](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/secret_rotation) | `infra/scripts/validate_enterprise_ops_evidence.sh` |
| baseline security posture | [security.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/security.md), [security_review_checklist.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/security_review_checklist.md), [security_governance.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/security_governance.md) | [evidence/access_reviews](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/access_reviews) + Section 11 ledger | `infra/scripts/validate_enterprise_ops_evidence.sh` |

## Known Limits
- Regional failover is not currently automated or rehearsed as a live failover.
- Mobile device validation still requires real hardware evidence outside the repo.
- This index is only credible if the linked evidence logs are actually maintained after drills and incidents.
