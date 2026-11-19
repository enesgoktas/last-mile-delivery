# Release Readiness Runbook

## Goal
Generate a single evidence bundle that validates backend, generated API contract artifacts, web, mobile, and infra checks before promotion.
The release gate should be at least as strict as the day-to-day CI lanes, not looser.

## Workflow
- Trigger [release-readiness.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/release-readiness.yml) from GitHub Actions.
- Wait for all jobs to pass:
  - `backend-validation`
  - `web-validation` (`frontend`, `tracking`)
  - `mobile-validation`
  - `infra-validation`

## What The Gate Verifies
- `backend-validation`: generated OpenAPI/TypeScript contract drift check, Ruff, Alembic migration smoke check, bytecode compile, pytest, and `pip-audit`.
- `web-validation`: `npm audit --audit-level=high`, TypeScript lint (`npm run lint`), tests, and build for `frontend` and `tracking`.
- `mobile-validation`: `npm audit --audit-level=high`, TypeScript lint, typecheck, and Jest tests.
- `infra-validation`: Section 11 evidence guard plus Terraform/security validation scripts.
- `infra-validation`: also validates backup controls and documented recovery posture.
- `infra-validation`: also validates enterprise ops evidence coverage (incident response, secret rotation, evidence index).

## Artifacts to Save
- `release-readiness-backend`
- `release-readiness-frontend`
- `release-readiness-tracking`
- `release-readiness-mobile`
- `release-readiness-infra`

The backend artifact now includes generated API contract validation output, separate Ruff and `pip-audit` logs, plus migration, compile, and pytest output. Web, tracking, and mobile artifacts include dependency audit logs in addition to lint/test/build output.
Infra artifacts now include backup-control and recovery-posture validation logs alongside Terraform validation output.
Infra artifacts also include enterprise-ops evidence validation output.

## Local Contract Refresh
- Run [shared/scripts/generate_api_contract.sh](/Users/dorukyalcin/Desktop/LastMileDelivery/shared/scripts/generate_api_contract.sh) after backend schema changes.
- Run [shared/scripts/check_api_contract.sh](/Users/dorukyalcin/Desktop/LastMileDelivery/shared/scripts/check_api_contract.sh) before pushing if you touched shared contracts, backend response schemas, or auth/tracking API shapes.

## Section 11 Evidence Updates
1. Add one row per gate/environment in [ledger.csv](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/section11/ledger.csv) with `result=pass` when artifacts and validations succeed.
2. Reference release-readiness artifact names in `artifact_refs` (semicolon separated).
3. Include reviewer and timestamp.

## Checklist Mapping
- Supports [11.4 CI/CD pipeline](../../../README.md) evidence (Section 11 table in root `README.md`).
- Supports Section 14 phase rollup evidence by proving full-stack validation on a release candidate commit.
- If any existing reusable CI workflow is stricter than release-readiness, update this runbook and the workflow in the same PR.
