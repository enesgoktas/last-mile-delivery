# Security and Data Retention Controls

## Scope
This document defines baseline controls for authentication, API integrations, sensitive delivery data, and retention for the Last-Mile Delivery System.

## Data Classes
- Authentication secrets: `JWT_SECRET`, refresh/access tokens, webhook signing secrets, API keys.
- Operational PII: customer email, customer phone, delivery address.
- Sensitive operational telemetry: courier live location snapshots, proof-of-delivery references.

## Storage and Transmission Controls
- TLS is required for all external traffic in staging and production.
- API keys are stored hashed (`bcrypt`) and never returned after creation.
- Webhook endpoint secrets are stored encrypted at rest and only returned once during endpoint creation.
- Tracking links are JWT-scoped, short-lived, and order-bound.
- POD artifacts are stored in S3 objects referenced by metadata, not embedded in DB payloads.

## Access Control and Auditability
- Role-based access controls enforced server-side for `ADMIN`, `DISPATCHER`, and `COURIER`.
- API key scope checks required for integration endpoints.
- Audit logging required for:
  - auth events (login/refresh/logout/register)
  - API key authenticated requests
  - security-sensitive state transitions and admin actions

## Retention Policy
- Location snapshots: retain 90 days, then purge.
- Webhook delivery logs: retain 90 days, then purge.
- Notifications and provider delivery metadata: retain 90 days, then purge.
- Audit logs: retain 365 days minimum.
- Expired tracking tokens: purge routinely (daily job) with supporting cleanup.
- POD references: retain 365 days by default unless legal or client contract requires longer.

Retention enforcement:
- Celery Beat task `app.workers.retention_tasks.run_data_retention_cleanup` runs every 24 hours.
- Retention windows are configured via:
  - `LOCATION_RETENTION_DAYS`
  - `WEBHOOK_LOG_RETENTION_DAYS`
  - `NOTIFICATION_RETENTION_DAYS`
  - `AUDIT_LOG_RETENTION_DAYS`

## Operational Requirements
- `WEBHOOK_SECRET_ENCRYPTION_KEY` must be set in staging and production and rotated via secrets manager.
- Secret rotation procedures follow [secret_rotation.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/secret_rotation.md).
- Governance expectations for vulnerability triage, access reviews, and break-glass access follow [security_governance.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/security_governance.md).
- Rate limiting required for auth and API-key public endpoints.
- Dependency vulnerability scanning required in CI for backend and web/mobile clients.

## Verification Checklist
- Security configuration present in each environment (`local`, `staging`, `production`).
- Unit/integration tests cover token rejection, scope rejection, and revoked credential paths.
- Alerting exists for repeated auth failures, webhook failure spikes, and API error spikes.
- Quarterly review of retention windows and data deletion jobs.
