# AWS Baseline IaC (Modular)

Terraform baseline for Last-Mile Delivery production topology:
- VPC, public/private subnets, IGW, optional NAT
- ALB + ECS Fargate services (API + worker)
- ECS blue/green deployment primitives (CodeDeploy + dual target groups)
- RDS PostgreSQL + ElastiCache Redis
- S3 buckets (dashboard, tracking, POD assets)
- ECR repositories
- Secrets Manager application secret bundle
- CloudWatch dashboard + operational alarms
- AWS budget guardrails and notifications

## Module Layout

- `modules/network`: VPC/subnets/route tables/NAT
- `modules/security`: ALB/ECS/RDS/Redis security groups
- `modules/data`: RDS, Redis, Secrets Manager
- `modules/storage`: S3 buckets and ECR repos
- `modules/compute`: ECS/ALB/task definitions/services/CodeDeploy
- `modules/observability`: dashboards and CloudWatch alarms
- `modules/finops`: AWS budget controls

Application runtime metrics:
- ECS API and worker tasks emit CloudWatch Embedded Metric Format (EMF) logs for dispatch, ETA, OSRM, and webhook runtime signals.
- The observability module dashboards and alarms consume those metrics from the `application_metrics_namespace` namespace.

## Remote State

This stack expects an S3 backend configured via backend config files:
- `environments/staging.backend.hcl`
- `environments/production.backend.hcl`

Bootstrap the remote state resources first:

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply -var-file=environments/staging.tfvars.example
```

Then initialize this stack:

```bash
cd ../aws
terraform init -backend-config=environments/staging.backend.hcl
```

Validation-only preflight (uses local tools if available, docker fallbacks otherwise):

```bash
infra/scripts/terraform_validate.sh
```

## Environment Files

- `environments/staging.tfvars.example`
- `environments/production.tfvars.example`
- `environments/staging.backend.hcl`
- `environments/production.backend.hcl`

## Notes

- If `enable_nat_gateway=false`, ECS tasks run in public subnets with public IPs.
- For production, keep `enable_nat_gateway=true` and use private subnets.
- Use `codedeploy_alarm_names` to enforce automatic rollback on alarm breach.
- Budget alert delivery requires either `budget_notification_emails` or `budget_notification_topic_arns`.
- `alb_certificate_arn` is required for HTTPS ALB listeners (prod and blue/green test routes).
- Sensitive variables (`db_password`, `jwt_secret`, `webhook_secret_encryption_key`) are intentionally excluded from checked-in tfvars files and must be supplied via `TF_VAR_*` or `-var`.

## GitHub Actions

- `infra-ci.yml`: Terraform fmt/init/validate + tflint + tfsec (blocking on HIGH/CRITICAL) + module checks
- `infra-plan.yml`: environment-scoped plans with artifacts
- `backend-cd.yml`: ECS blue/green deployment with migration and health gates
