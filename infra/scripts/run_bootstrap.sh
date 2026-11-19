#!/usr/bin/env bash
# run_bootstrap.sh — initialise and apply the Terraform bootstrap module.
#
# Creates the S3 state bucket and DynamoDB lock table that all subsequent
# Terraform runs depend on.  Must be run once per AWS account before any
# other Terraform commands.
#
# Usage:
#   ./run_bootstrap.sh [environment]
#
#   environment   staging (default) | production
#
# The script stops before terraform apply and asks for confirmation.
# Pass AUTO_APPROVE=1 to skip the prompt (CI usage).
#
# Optional env vars:
#   AWS_REGION   override target region (default: value in tfvars)
#   AWS_PROFILE  use a named AWS CLI profile

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_DIR="${SCRIPT_DIR}/../terraform/bootstrap"
ENV="${1:-staging}"
TFVARS_FILE="${BOOTSTRAP_DIR}/environments/${ENV}.tfvars"
AUTO_APPROVE="${AUTO_APPROVE:-0}"

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

TF_VERSION="$(terraform version -json | python3 -c "import sys,json; print(json.load(sys.stdin)['terraform_version'])" 2>/dev/null || terraform version | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
echo "Terraform version: ${TF_VERSION}"

# ---------------------------------------------------------------------------
# Validate environment and tfvars
# ---------------------------------------------------------------------------
if [[ "${ENV}" != "staging" && "${ENV}" != "production" ]]; then
  echo "ERROR: environment must be 'staging' or 'production', got '${ENV}'." >&2
  exit 1
fi

if [[ ! -f "${TFVARS_FILE}" ]]; then
  echo "ERROR: tfvars file not found: ${TFVARS_FILE}" >&2
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
echo ""

# ---------------------------------------------------------------------------
# terraform init
# ---------------------------------------------------------------------------
echo "==> terraform init"
cd "${BOOTSTRAP_DIR}"
terraform init -upgrade

echo ""

# ---------------------------------------------------------------------------
# terraform plan
# ---------------------------------------------------------------------------
echo "==> terraform plan (${ENV})"
terraform plan \
  -var-file="environments/${ENV}.tfvars" \
  -out=bootstrap.tfplan

echo ""

# ---------------------------------------------------------------------------
# Confirmation gate
# ---------------------------------------------------------------------------
if [[ "${AUTO_APPROVE}" != "1" ]]; then
  echo "Review the plan above."
  read -rp "Apply bootstrap infrastructure for '${ENV}'? [yes/no]: " CONFIRM
  if [[ "${CONFIRM}" != "yes" ]]; then
    echo "Aborted. bootstrap.tfplan written but not applied."
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# terraform apply
# ---------------------------------------------------------------------------
echo ""
echo "==> terraform apply"
terraform apply bootstrap.tfplan

# ---------------------------------------------------------------------------
# Output summary
# ---------------------------------------------------------------------------
echo ""
echo "================================================================="
echo " Bootstrap complete. Resources created:"
echo "================================================================="
terraform output
echo ""
echo " Use these values in subsequent steps:"
STATE_BUCKET="$(terraform output -raw state_bucket_name 2>/dev/null || echo '<see output above>')"
LOCK_TABLE="$(terraform output -raw lock_table_name 2>/dev/null || echo '<see output above>')"
echo "   TF_STATE_BUCKET     = ${STATE_BUCKET}"
echo "   TF_STATE_LOCK_TABLE = ${LOCK_TABLE}"
echo ""
echo " Add them to GitHub Actions Secrets and to the main module backend config:"
echo "   infra/terraform/aws/environments/${ENV}.backend.hcl"
echo "================================================================="
