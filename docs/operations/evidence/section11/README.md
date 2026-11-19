# Section 11 Evidence Contract

This directory is the single source of truth for Section 11 completion evidence.

## Ledger Format

Use `ledger.csv` with columns:
1. `run_id`: unique workflow or validation run identifier
2. `commit_sha`: commit under validation
3. `environment`: `staging` or `production`
4. `gate_id`: one of `11.1`, `11.2`, `11.3`, `11.4`
5. `artifact_refs`: semicolon-separated artifact IDs/links/names
6. `result`: `pass`, `fail`, or `not_run`
7. `reviewer`: accountable reviewer/owner
8. `timestamp_utc`: ISO-8601 UTC timestamp

## Checklist Gate Mapping

- `11.1 AWS architecture IaC`
  - Required evidence: `infra/scripts/terraform_validate.sh`, `infra-ci` run, `infra-plan` artifacts for staging+production, deployment validation evidence.
- `11.2 Cost optimization controls`
  - Required evidence: fixture preflight (`monthly_cost_report_test.sh`), live monthly cost report artifact, budget threshold validation (`validate_budget_alerts.sh`).
- `11.3 Monitoring/observability`
  - Required evidence: request-ID regression tests, required alarm validation (`validate_required_alarms.sh`), fire-drill execution evidence.
- `11.4 CI/CD pipeline`
  - Required evidence: workflow runs for backend/web-mobile/infra/release-readiness, migration and health gate logs, workflow guardrail checks.

## Completion Rules

- Section 11 checklist rows can move to `done` only when both `staging` and `production` have `pass` rows in `ledger.csv` for each gate ID.
- `infra/scripts/check_section11_evidence.sh` enforces this rule.
