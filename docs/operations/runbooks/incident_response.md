# Incident Response Runbook

## Severity Levels
- `SEV-1`: active customer-facing outage, data-loss event, or security event requiring immediate command and broad communication.
- `SEV-2`: major feature degradation or high-risk operational failure with meaningful customer impact.
- `SEV-3`: localized or recoverable issue with limited customer impact.
- `SEV-4`: low-risk defect, alert noise, or issue that can wait for normal business-hours handling.

## Roles
- Incident commander: owns coordination, timeline, and decision-making.
- Technical lead: owns diagnosis and mitigation execution.
- Communications owner: owns customer and stakeholder updates.
- Scribe: records timestamps, actions, and evidence references.

One person may temporarily cover multiple roles in a small-team incident, but the responsibilities must still be assigned explicitly.

## First 30 Minutes
1. Declare the severity and open an incident record.
2. Assign incident commander, technical lead, communications owner, and scribe.
3. Stabilize the system first: stop risky deploys, pause destructive jobs, and preserve evidence before destructive recovery actions.
4. Identify the blast radius:
   - auth and API availability
   - worker backlog
   - order/routing correctness
   - tracking availability
   - notification/webhook delivery
5. Choose the initial mitigation path:
   - rollback or redeploy
   - service restart
   - recovery runbook execution
   - secret rotation or credential revocation
6. Send the first status update with severity, affected scope, and next update time.

## Diagnostic Checklist
- Check current deploy/release status and recent workflow changes.
- Check dashboards, alarms, and request/worker correlation IDs.
- Confirm whether the source of truth is intact:
  - PostgreSQL
  - S3/POD assets
  - Redis runtime state
- Confirm whether the incident is operational, code/regression, or security-related.
- If the event is security-sensitive, branch immediately to [secret_rotation.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/secret_rotation.md) and preserve forensic evidence.

## Communication Rules
- `SEV-1` and `SEV-2` incidents require explicit update cadence and named owner.
- Never claim a root cause before it is verified.
- Never rotate or revoke customer/integration credentials without recording affected tenants and rollback plan.
- Customer-facing status updates should state impact, mitigation state, and next expected update time.

## Evidence Capture
- Timeline with UTC timestamps.
- Relevant request IDs, worker task IDs, webhook IDs, or optimization run IDs.
- Alarm screenshots or exported alarm JSON.
- Release artifact or deploy identifiers if a rollout is involved.
- Recovery or rollback command output if used.
- Follow-up tickets for every unresolved gap or missed target.

Store incident artifacts in [docs/operations/evidence/incidents](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/incidents) and log every event in [incident_response_execution_log.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/incident_response_execution_log.md).

## Exit Criteria
- Service is stable or intentionally degraded with documented workaround.
- Customer-impact statement is updated.
- Actual mitigation and resolution times are recorded.
- Follow-up work items exist for root-cause remediation.
- If recovery targets were missed, the incident links back to the disaster recovery or backup restore evidence trail.
