# Webhook Dead-Letter Tabletop

- Conducted at: `2026-03-27T05:10:29Z`
- Environment: `tabletop`
- Scenario: `webhook-dead-letter-high` alarm after a release candidate deploy
- Facilitator: `codex`
- Result: `PASS_WITH_FOLLOW_UP`

## Objective
Verify that the incident-response runbook, correlation tracing, and webhook retry/dead-letter visibility are sufficient to coordinate a customer-impacting webhook failure.

## Exercise Summary
1. Assume the runtime webhook dead-letter alarm fires shortly after a release candidate reaches staging.
2. Pause further promotion and open an incident record as `SEV-2`.
3. Use the request/correlation chain to identify the originating HTTP request, worker tasks, and retry path.
4. Confirm whether the issue is code/regression, provider outage, or bad endpoint configuration.
5. Decide whether to replay dead-lettered deliveries, roll back the release, or disable the affected endpoint.

## Expected Operator Actions
- Check [incident_response.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/incident_response.md) for severity, role assignment, and first-30-minute actions.
- Use [monitoring_observability.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/monitoring_observability.md) to follow:
  - `X-Request-ID`
  - `X-Correlation-ID`
  - parent Celery task IDs
- Review webhook retry/dead-letter behavior already covered in backend integration tests and worker logs.
- Record the incident timeline before replaying or changing credentials.

## Outcome
- The runbook and tracing chain were sufficient for a tabletop-level response.
- The biggest remaining gap is still live alarm delivery verification against the eventual deployment platform.

## Follow-Up
- Run a real staging webhook dead-letter drill after the deployment platform is chosen.
- Capture the resulting alarm notification evidence in the alarm drill log.
