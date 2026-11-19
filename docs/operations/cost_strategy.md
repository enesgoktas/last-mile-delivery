# Cost Optimization Strategy

## Goals
- Keep fixed baseline predictable for staging and early production.
- Prefer open-source/self-hosted routing/geocoding over per-request pricing.
- Scale compute tiers only when throughput and latency thresholds justify it.

## Baseline Targets
- Early stage target: under `$120/month` non-production and under `$350/month` initial production.
- Cost KPI: cost per completed order tracked weekly.
- Guardrail: no unmanaged spend growth above `15%` week-over-week without an approved change request.

## Cost Levers

| Area | Strategy | Trigger to Scale |
|---|---|---|
| API/Worker compute | Fargate right-sizing, separate API and worker desired counts | p95 API latency > target or queue depth sustained > threshold |
| Routing/geocoding | Self-hosted OSRM/Photon; avoid per-call map APIs | Coverage expansion to new geographies |
| Database | Start small (`db.t4g.micro/small`), add storage autoscaling | sustained CPU > 70% or storage > 75% |
| Redis | single-node early stage, replica on growth | cache hit ratio falls, failover requirements increase |
| Storage | S3 lifecycle and intelligent tiering for POD artifacts | monthly storage growth > planned budget |
| CI/CD | cache dependencies, avoid redundant matrix runs | CI minutes exceed monthly budget trend |

## Concrete Controls
- Enable ECR lifecycle policies (keep latest 25 images).
- Enforce S3 lifecycle transition on POD bucket after 30 days.
- Enable AWS Budgets monthly guardrail and 80%/100% alert thresholds.
- Validate budgets and subscribers with `infra/scripts/validate_budget_alerts.sh`.
- Use CloudWatch alarms on ECS/RDS/Redis utilization to avoid overprovisioning by guesswork.
- Periodically review ECS task CPU/memory reservations against real usage.
- Keep NAT count minimal; single NAT for staging, multi-AZ only where justified.

Infrastructure source of truth:
- Terraform budget controls: [infra/terraform/aws/budgets.tf](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/terraform/aws/budgets.tf)
- Environment defaults: [staging.tfvars.example](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/terraform/aws/environments/staging.tfvars.example), [production.tfvars.example](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/terraform/aws/environments/production.tfvars.example)

## Monthly Cost Review
1. Compare current spend against baseline budget by service (ECS, RDS, Redis, S3, data transfer).
2. Track unit economics: `monthly spend / completed deliveries`.
3. Identify top 3 regressions and open follow-up actions with owner and due date.
4. Validate lifecycle and retention jobs executed as expected.
5. Validate budget thresholds/subscribers with `validate_budget_alerts.sh` and record output artifact.
6. Record decisions in release notes for financial traceability.
7. Capture findings in [monthly_cost_review.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/monthly_cost_review.md).

Automation helpers:
- [infra/scripts/monthly_cost_report.sh](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/scripts/monthly_cost_report.sh)
- [infra/scripts/validate_budget_alerts.sh](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/scripts/validate_budget_alerts.sh)
- [cost-report.yml](/Users/dorukyalcin/Desktop/LastMileDelivery/.github/workflows/cost-report.yml)

## Ownership
- Platform owns infra and FinOps controls.
- Backend owns workload efficiency (query/index/task efficiency).
- Product/Operations approve threshold changes that materially affect SLA vs cost.
