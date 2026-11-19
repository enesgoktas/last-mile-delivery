#!/usr/bin/env bash
# run_terraform_init_plan.sh — initialise and plan the main AWS Terraform module.
#
# Mirrors what infra-plan.yml does in CI but runs locally.  Sensitive values
# (DB password, JWT secret, webhook key, ACM certificate ARN) are read from
# environment variables and written to a temporary override file that is
# deleted on exit — they are never stored in staging.tfvars.
#
# Prerequisites:
#   1. Bootstrap module applied (run_bootstrap.sh).
#   2. AWS credentials configured (OIDC or access key).
#   3. Required env vars set (see below).
#
# Required env vars:
#   TF_DB_PASSWORD                   PostgreSQL master password
#   TF_JWT_SECRET                    JWT signing secret
#   TF_WEBHOOK_SECRET_ENCRYPTION_KEY Webhook payload encryption key
#   TF_ALB_CERTIFICATE_ARN           ACM certificate ARN for ALB HTTPS listener
#
# Optional env vars:
#   TF_STATE_BUCKET      override S3 bucket for remote state (default from backend.hcl)
#   TF_STATE_LOCK_TABLE  override DynamoDB table (default from backend.hcl)
#   AWS_REGION           target region (default: us-east-1)
#   AWS_PROFILE          named AWS CLI profile
#   ENV                  staging (default) | production
#
# Usage:
#   export TF_DB_PASSWORD=...
#   export TF_JWT_SECRET=...
#   export TF_WEBHOOK_SECRET_ENCRYPTION_KEY=...
#   export TF_ALB_CERTIFICATE_ARN=arn:aws:acm:us-east-1:123456789012:certificate/...
#   ./run_terraform_init_plan.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="${SCRIPT_DIR}/../terraform/aws"
ENV="${ENV:-staging}"
REGION="${AWS_REGION:-us-east-1}"
OVERRIDE_FILE="${MODULE_DIR}/ci-secrets.override.tfvars"

# ---------------------------------------------------------------------------
# Cleanup: remove the secrets override file on exit (success or failure)
# ---------------------------------------------------------------------------
cleanup() {
  if [[ -f "${OVERRIDE_FILE}" ]]; then
    rm -f "${OVERRIDE_FILE}"
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
if ! command -v terraform >/dev/null 2>&1; then
  echo "ERROR: terraform is required (>= 1.6.0). https://developer.hashicorp.com/terraform/install" >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: aws CLI is required." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Validate required env vars
# ---------------------------------------------------------------------------
MISSING=()
[[ -z "${TF_DB_PASSWORD:-}" ]]                   && MISSING+=("TF_DB_PASSWORD")
[[ -z "${TF_JWT_SECRET:-}" ]]                    && MISSING+=("TF_JWT_SECRET")
[[ -z "${TF_WEBHOOK_SECRET_ENCRYPTION_KEY:-}" ]] && MISSING+=("TF_WEBHOOK_SECRET_ENCRYPTION_KEY")
[[ -z "${TF_ALB_CERTIFICATE_ARN:-}" ]]           && MISSING+=("TF_ALB_CERTIFICATE_ARN")

if (( ${#MISSING[@]} > 0 )); then
  echo "ERROR: the following environment variables must be set:" >&2
  printf '  %s\n' "${MISSING[@]}" >&2
  echo "" >&2
  echo "  Tip: run provision_staging_secrets.sh first — it prints TF_JWT_SECRET," >&2
  echo "  TF_WEBHOOK_SECRET_ENCRYPTION_KEY, and TF_DB_PASSWORD." >&2
  exit 1
fi

if [[ "${ENV}" != "staging" && "${ENV}" != "production" ]]; then
  echo "ERROR: ENV must be 'staging' or 'production', got '${ENV}'." >&2
  exit 1
fi

BACKEND_HCL="${MODULE_DIR}/environments/${ENV}.backend.hcl"
TFVARS_FILE="${MODULE_DIR}/environments/${ENV}.tfvars"

if [[ ! -f "${BACKEND_HCL}" ]]; then
  echo "ERROR: backend config not found: ${BACKEND_HCL}" >&2
  exit 1
fi

if [[ ! -f "${TFVARS_FILE}" ]]; then
  echo "ERROR: tfvars not found: ${TFVARS_FILE}" >&2
  echo "  Copy environments/${ENV}.tfvars.example to environments/${ENV}.tfvars and edit as needed." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Confirm AWS identity
# ---------------------------------------------------------------------------
echo "Verifying AWS credentials..."
CALLER="$(aws sts get-caller-identity --output text --query 'Arn' 2>&1)" || {
  echo "ERROR: AWS credentials not configured or not valid." >&2
  exit 1
}
echo "  Authenticated as: ${CALLER}"
echo "  Region:           ${REGION}"
echo "  Environment:      ${ENV}"
echo ""

# ---------------------------------------------------------------------------
# Write temporary secrets override (deleted on EXIT via trap)
# ---------------------------------------------------------------------------
cat > "${OVERRIDE_FILE}" <<VARS
db_password                   = "${TF_DB_PASSWORD}"
jwt_secret                    = "${TF_JWT_SECRET}"
webhook_secret_encryption_key = "${TF_WEBHOOK_SECRET_ENCRYPTION_KEY}"
alb_certificate_arn           = "${TF_ALB_CERTIFICATE_ARN}"
VARS

# ---------------------------------------------------------------------------
# Build backend config args (allow env var overrides for bucket/table)
# ---------------------------------------------------------------------------
BACKEND_ARGS=(
  "-backend-config=${BACKEND_HCL}"
  "-backend-config=region=${REGION}"
)
[[ -n "${TF_STATE_BUCKET:-}" ]]     && BACKEND_ARGS+=("-backend-config=bucket=${TF_STATE_BUCKET}")
[[ -n "${TF_STATE_LOCK_TABLE:-}" ]] && BACKEND_ARGS+=("-backend-config=dynamodb_table=${TF_STATE_LOCK_TABLE}")

# ---------------------------------------------------------------------------
# terraform init
# ---------------------------------------------------------------------------
echo "==> terraform init (${ENV})"
cd "${MODULE_DIR}"
terraform init -upgrade "${BACKEND_ARGS[@]}"

echo ""

# ---------------------------------------------------------------------------
# terraform fmt check
# ---------------------------------------------------------------------------
echo "==> terraform fmt check"
if ! terraform fmt -check -recursive; then
  echo "" >&2
  echo "  Format issues found. Run: terraform fmt -recursive infra/terraform/aws/" >&2
  echo "  Then commit the formatted files." >&2
  exit 1
fi

echo ""

# ---------------------------------------------------------------------------
# terraform validate
# ---------------------------------------------------------------------------
echo "==> terraform validate"
terraform validate

echo ""

# ---------------------------------------------------------------------------
# terraform plan
# ---------------------------------------------------------------------------
echo "==> terraform plan (${ENV})"
terraform plan \
  -input=false \
  -lock=true \
  -var-file="environments/${ENV}.tfvars" \
  -var-file="${OVERRIDE_FILE}" \
  -out=tfplan

echo ""
echo "Plan written to: ${MODULE_DIR}/tfplan"
echo ""
echo "================================================================="
echo " Review the plan above carefully before applying:"
echo "   - RDS instance class and storage match expectations"
echo "   - ElastiCache node type is correct"
echo "   - ALB listener is HTTPS (port 443) with ACM certificate"
echo "   - No unexpected resource replacements or deletions"
echo "================================================================="
echo ""
echo " To render a human-readable plan text:"
echo "   cd infra/terraform/aws && terraform show -no-color tfplan"
echo ""
echo " To apply (after review):"
echo "   cd infra/terraform/aws && terraform apply tfplan"
echo "================================================================="
