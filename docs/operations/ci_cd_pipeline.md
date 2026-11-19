# CI/CD Pipeline Runbook

## Workflow Entry Points
- [backend-ci.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/backend-ci.yml): entry workflow for backend CI (`workflow_dispatch`, PR, push).
- [web-mobile-ci.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/web-mobile-ci.yml): entry workflow for frontend/tracking/mobile CI.
- [infra-ci.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/infra-ci.yml): entry workflow for Terraform + security scanning.
- [infra-plan.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/infra-plan.yml): manual environment-scoped Terraform plan with remote backend.
- [backend-cd.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/backend-cd.yml): backend deployment via ECS blue/green CodeDeploy.
- [release-readiness.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/release-readiness.yml): one-click full-stack validation evidence bundle.
- [workflow-lint.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/workflow-lint.yml): CI gate for Actions workflow syntax (`actionlint`).

## Reusable Workflow Modules
- [reusable-backend-ci.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/reusable-backend-ci.yml)
- [reusable-web-mobile-ci.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/reusable-web-mobile-ci.yml)
- [reusable-infra-ci.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/reusable-infra-ci.yml)
- [reusable-release-readiness.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/reusable-release-readiness.yml)

## Required GitHub Secrets

### Shared AWS OIDC
- `AWS_REGION`
- `AWS_ROLE_TO_ASSUME`

### Terraform Plan
- `TF_STATE_BUCKET`
- `TF_STATE_LOCK_TABLE`
- `TF_DB_PASSWORD`
- `TF_JWT_SECRET`
- `TF_WEBHOOK_SECRET_ENCRYPTION_KEY`
- `TF_ALB_CERTIFICATE_ARN`

### Backend CD
- `ECS_CLUSTER`
- `ECS_API_SERVICE`
- `ECS_WORKER_SERVICE`
- `ECR_BACKEND_REPOSITORY`
- `ECR_WORKER_REPOSITORY`
- `CODEDEPLOY_API_APPLICATION`
- `CODEDEPLOY_API_DEPLOYMENT_GROUP`
- `ECS_MIGRATION_TASK_DEFINITION_ARN` (optional)
- `ECS_SUBNETS_CSV` / `ECS_SECURITY_GROUPS_CSV` (required if migration task enabled)
- `API_HEALTHCHECK_URL` (optional)

## Deployment Flow
1. Run local preflight for infra changes: `infra/scripts/terraform_validate.sh`.
2. Run `infra-ci` and confirm tflint/tfsec/module checks pass.
3. Run `infra-plan` for `staging` and review plan artifacts.
4. Apply infra changes through approved Terraform apply process.
5. Run `backend-ci` and `web-mobile-ci` on release candidate commit.
6. Trigger `release-readiness` and archive artifacts.
7. Trigger `backend-cd` to `staging`; verify migration gate, CodeDeploy success, and API health gate.
8. Promote to `production` using same evidence-driven flow.

## Failure Gates
- Migration task exit code non-zero blocks deployment.
- CodeDeploy deployment failure or rollback event blocks deployment.
- API health check gate failure blocks deployment.
- Terraform plan failures block infrastructure promotion.
- Section 11 evidence guard (`infra/scripts/check_section11_evidence.sh`) blocks invalid checklist status transitions.
- Enterprise ops evidence guard (`infra/scripts/validate_enterprise_ops_evidence.sh`) blocks promotion if incident-response, secret-rotation, or evidence-index artifacts drift.

## Approval Governance
- Follow [deployment_governance.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/deployment_governance.md) for staging and production approval rules.
- Production promotion requires a two-person rule and a documented rollback plan.
- Store promotion evidence in [evidence/deployment_approvals](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/deployment_approvals).

## Pipeline Safety Guards
- OIDC role assumption (no long-lived AWS access keys in workflows).
- Least-privilege GitHub permissions (`contents: read`, `id-token: write` only where needed).
- Concurrency control to prevent stale duplicate runs.
- Job-level timeouts on all CI/CD/plan workflows.
- Artifact upload on success/failure for diagnosability.

## Evidence Recording
- Record Section 11 run outcomes in [docs/operations/evidence/section11/ledger.csv](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/section11/ledger.csv).
- Only mark Section 11 checklist rows as `done` after both staging and production pass evidence exists for each gate.
