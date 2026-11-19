# Environment Matrix and Secrets

## Environments
- `local`: docker compose and developer seed data.
- `staging`: production-like integration tests and UAT.
- `production`: customer traffic.

## Required secrets
- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `TRACKING_JWT_SECRET`
- `OIDC_SECRET_ENCRYPTION_KEY`
- `WEBHOOK_SECRET_ENCRYPTION_KEY`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `AWS_REGION`
- `S3_BUCKET`
- `TWILIO_*`
- `SES_*`
- `FCM_*`
- `EXPO_PUSH_ACCESS_TOKEN` (if Expo push delivery is enabled)

## Rotation-window secrets
- `AUTH_JWT_ACTIVE_KID`
- `AUTH_JWT_PREVIOUS_KEYS`
- `TRACKING_JWT_ACTIVE_KID`
- `TRACKING_JWT_PREVIOUS_KEYS`
- `OIDC_SECRET_ENCRYPTION_KEY`
- `WEBHOOK_SECRET_ENCRYPTION_KEY`

## CI/CD-only secrets
- Terraform plan workflow:
  - `AWS_ROLE_TO_ASSUME`
  - `TF_STATE_BUCKET`
  - `TF_STATE_LOCK_TABLE`
  - `TF_DB_PASSWORD`
  - `TF_JWT_SECRET`
  - `TF_WEBHOOK_SECRET_ENCRYPTION_KEY`
  - `TF_ALB_CERTIFICATE_ARN`
- Backend CD workflow:
  - `AWS_ROLE_TO_ASSUME`
  - `ECS_CLUSTER`
  - `ECS_API_SERVICE`
  - `ECS_WORKER_SERVICE`
  - `ECR_BACKEND_REPOSITORY`
  - `ECR_WORKER_REPOSITORY`
  - `CODEDEPLOY_API_APPLICATION`
  - `CODEDEPLOY_API_DEPLOYMENT_GROUP`
  - `ECS_MIGRATION_TASK_DEFINITION_ARN` (optional)
  - `ECS_SUBNETS_CSV`
  - `ECS_SECURITY_GROUPS_CSV`
  - `API_HEALTHCHECK_URL` (optional)

## Recovery-only operator secrets
- `MAINTENANCE_DATABASE_URL`
  - Administrative PostgreSQL connection used for backup/restore drills and emergency database recreation.
  - Must not be used by the normal application runtime.

## Required non-secret runtime config
- `CORS_ALLOWED_ORIGINS` (explicit origin allowlist for dashboard/tracking/mobile web)
- `CORS_ALLOW_CREDENTIALS`
- `AUTH_RATE_LIMIT_PER_MIN`
- `LOCATION_RETENTION_DAYS`
- `WEBHOOK_LOG_RETENTION_DAYS`
- `NOTIFICATION_RETENTION_DAYS`
- `AUDIT_LOG_RETENTION_DAYS`

## Rules
- Secrets never committed.
- Rotation required for compromised or expired credentials.
- Rotation events must be logged in the secret rotation execution log with evidence references.
