# Monthly Cost Review Template

## Report Generation
- Live AWS Cost Explorer report:
  - `infra/scripts/monthly_cost_report.sh <start-date> <end-date> docs/operations/reports/<month>.md`
- Budget guardrail validation:
  - `infra/scripts/validate_budget_alerts.sh <budget-name> 80 100 docs/operations/reports/<month>.budget-validation.json`
- Fixture-based dry run (no AWS call):
  - `COST_REPORT_INPUT_JSON=infra/scripts/fixtures/cost_explorer_sample.json infra/scripts/monthly_cost_report.sh 2026-01-01 2026-02-01 /tmp/2026-01.sample-cost-report.md`
  - `BUDGET_VALIDATION_INPUT_JSON=infra/scripts/fixtures/budget_notifications_sample.json infra/scripts/validate_budget_alerts.sh last-mile-delivery-staging-monthly-total 80 100 /tmp/budget-validation.json`

## Metadata
- Month:
- Environment(s):
- Reviewer:
- Date:

## Spend Summary (AWS Cost Explorer)
- Total spend:
- ECS:
- RDS:
- ElastiCache:
- S3:
- Data transfer:

## Budget Guardrails
- Budget configured:
- Actual threshold crossed (80%):
- Forecast threshold crossed (100%):
- Alert timestamps:
- Validation artifact path:

## Unit Economics
- Completed deliveries:
- Cost per completed delivery:
- Week-over-week delta:

## Top 3 Regressions
1. Service:
   - Root cause:
   - Owner:
   - Due date:
2. Service:
   - Root cause:
   - Owner:
   - Due date:
3. Service:
   - Root cause:
   - Owner:
   - Due date:

## Actions
1.
2.
3.

## Signoff
- Platform owner:
- Product/Ops approver:
- Section 11 evidence updated in [ledger.csv](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/section11/ledger.csv):
