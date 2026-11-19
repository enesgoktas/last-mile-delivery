#!/usr/bin/env bash
# provision_staging_secrets.sh — bootstrap AWS Secrets Manager entries for staging.
#
# Creates all secrets required by the Last-Mile Delivery backend before Terraform apply.
# Safe to re-run: existing secrets are skipped, never overwritten.
#
# Usage:
#   DB_PASSWORD=<password> ./provision_staging_secrets.sh
#   ./provision_staging_secrets.sh          # prompts interactively for DB_PASSWORD
#
# After completion, copy the printed TF_* values to GitHub Actions secrets:
#   GitHub repo → Settings → Secrets and variables → Actions
#
# Optional env vars:
#   AWS_REGION   override target region (default: us-east-1)
#   AWS_PROFILE  use a named AWS CLI profile

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REGION="${AWS_REGION:-us-east-1}"
ENV="staging"
PREFIX="lastmile/${ENV}"
PROJECT="last-mile-delivery"

TAGS="[{\"Key\":\"Environment\",\"Value\":\"${ENV}\"},{\"Key\":\"Project\",\"Value\":\"${PROJECT}\"},{\"Key\":\"ManagedBy\",\"Value\":\"provision_staging_secrets.sh\"}]"

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: aws CLI is required." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required (used to generate secrets)." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Confirm AWS identity (gives fast feedback on missing/wrong credentials)
# ---------------------------------------------------------------------------
echo "Verifying AWS credentials..."
CALLER="$(aws sts get-caller-identity --region "${REGION}" --output text --query 'Arn' 2>&1)" || {
  echo "ERROR: AWS credentials not configured or not valid. Run 'aws configure' first." >&2
  exit 1
}
echo "  Authenticated as: ${CALLER}"
echo "  Region:           ${REGION}"
echo ""

# ---------------------------------------------------------------------------
# Helper: create a secret if it does not already exist
# ---------------------------------------------------------------------------
create_secret() {
  local name="$1"
  local value="$2"
  local description="$3"

  if aws secretsmanager describe-secret \
       --secret-id "${name}" \
       --region "${REGION}" \
       >/dev/null 2>&1; then
    echo "  [SKIP]    ${name}  (already exists)"
  else
    aws secretsmanager create-secret \
      --name "${name}" \
      --description "${description}" \
      --secret-string "${value}" \
      --region "${REGION}" \
      --tags "${TAGS}" \
      >/dev/null
    echo "  [CREATED] ${name}"
  fi
}

# ---------------------------------------------------------------------------
# Generate auto-rotatable secrets
# ---------------------------------------------------------------------------
JWT_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(32))")"
WEBHOOK_KEY="$(python3 -c "import secrets; print(secrets.token_hex(32))")"

# ---------------------------------------------------------------------------
# DB password — env var or interactive prompt
# ---------------------------------------------------------------------------
if [[ -z "${DB_PASSWORD:-}" ]]; then
  echo "Enter a strong DB master password (or set DB_PASSWORD env var to skip prompt):"
  read -rsp "DB_PASSWORD: " DB_PASSWORD
  echo ""
fi

if [[ -z "${DB_PASSWORD:-}" ]]; then
  echo "ERROR: DB_PASSWORD is required." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Provision secrets
# ---------------------------------------------------------------------------
echo "Provisioning staging secrets in region ${REGION}..."
echo ""

# Core secrets (auto-generated or operator-supplied)
create_secret \
  "${PREFIX}/jwt_secret" \
  "${JWT_SECRET}" \
  "JWT signing secret for Last-Mile backend (${ENV})"

create_secret \
  "${PREFIX}/db_password" \
  "${DB_PASSWORD}" \
  "PostgreSQL master password (${ENV})"

create_secret \
  "${PREFIX}/webhook_encryption_key" \
  "${WEBHOOK_KEY}" \
  "Webhook payload encryption key (${ENV})"

# Optional integration secrets — update placeholder values with real credentials before
# applying Terraform.  Re-run the AWS CLI update-secret command shown at the end of this
# script to replace each REPLACE_ME value.
create_secret \
  "${PREFIX}/ses_from_email" \
  "noreply@example.com" \
  "SES sender email address (${ENV}) — replace with real address"

create_secret \
  "${PREFIX}/twilio_account_sid" \
  "REPLACE_ME" \
  "Twilio account SID (${ENV}) — replace with real value"

create_secret \
  "${PREFIX}/twilio_auth_token" \
  "REPLACE_ME" \
  "Twilio auth token (${ENV}) — replace with real value"

create_secret \
  "${PREFIX}/twilio_from_number" \
  "+10000000000" \
  "Twilio sender number (${ENV}) — replace with real E.164 number"

create_secret \
  "${PREFIX}/fcm_server_key" \
  "REPLACE_ME" \
  "Firebase Cloud Messaging server key (${ENV}) — replace with real value"

create_secret \
  "${PREFIX}/ors_api_key" \
  "REPLACE_ME" \
  "OpenRouteService API key (${ENV}) — replace with real value"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
echo ""
echo "================================================================="
echo " Copy the values below into GitHub Actions Secrets:"
echo " GitHub repo → Settings → Secrets and variables → Actions"
echo "================================================================="
echo ""
echo "  TF_JWT_SECRET                    = ${JWT_SECRET}"
echo "  TF_WEBHOOK_SECRET_ENCRYPTION_KEY = ${WEBHOOK_KEY}"
echo "  TF_DB_PASSWORD                   = ${DB_PASSWORD}"
echo ""
echo "  TF_STATE_BUCKET                  = last-mile-delivery-terraform-state"
echo "  TF_STATE_LOCK_TABLE              = last-mile-delivery-terraform-locks"
echo "  AWS_REGION                       = ${REGION}"
echo ""
echo "  (Set TF_ALB_CERTIFICATE_ARN, AWS_ROLE_TO_ASSUME, and ECS/CodeDeploy"
echo "   values once Terraform apply has completed.)"
echo ""
echo "================================================================="
echo " To replace a placeholder secret value later:"
echo "   aws secretsmanager update-secret \\"
echo "     --secret-id ${PREFIX}/twilio_account_sid \\"
echo "     --secret-string '<real-value>'"
echo "================================================================="
echo ""
echo "Verify all secrets:"
echo "  aws secretsmanager list-secrets --region ${REGION} \\"
echo "    --query \"SecretList[?contains(Name,'${PREFIX}')].[Name]\" --output table"
