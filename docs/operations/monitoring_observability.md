# Monitoring and Observability

## Scope
Observability baseline for API, workers, database, cache, and delivery-critical integrations.

## Signals
- Metrics: latency, throughput, errors, queue depth, resource saturation.
- Logs: structured JSON request logs, task execution logs, webhook/notification outcomes.
- Traces: long-running optimization and webhook pipelines.

Application export:
- `/api/v1/analytics/runtime-performance` remains the authenticated JSON snapshot for operator-facing debugging.
- `/api/v1/analytics/runtime-performance/prometheus` exposes authenticated Prometheus-style text for scrape jobs.
- Current app-level metrics include dispatch latency, ETA latency/error delta, OSRM counters/latency, and webhook enqueue/retry/dead-letter delivery signals.
- ECS API and worker tasks also emit CloudWatch Embedded Metric Format lines directly to CloudWatch Logs, which produces custom metrics in the `LastMile/Application` namespace with `Application`, `Environment`, and `Service` dimensions.
- Celery enqueue paths propagate `X-Request-ID`, `X-Correlation-ID`, and parent task IDs into task headers, and worker entry points re-bind that context so API logs, optimization jobs, webhook retries, and downstream worker logs share the same correlation chain.

## Core Dashboards
- API health: request count, p95 latency, 4xx/5xx split.
- Dispatch/optimization: optimization run duration, unassigned counts, retries.
- Infrastructure: ECS CPU/memory, RDS CPU/storage connections, Redis memory/evictions.
- Delivery UX: tracking poll success rate, webhook success rate, notification failure rate.
- Runtime metrics: dispatch batch/on-demand p95 latency, ETA compute p95 latency, and webhook enqueue/success/retry/dead-letter counts by service.

## Alarms
- ALB target 5xx count above threshold.
- ALB response time elevated.
- ALB healthy target count below minimum.
- API ECS CPU high.
- Worker ECS CPU/memory high.
- RDS CPU high.
- RDS connection count high.
- Redis memory usage high.
- Redis key evictions above zero.
- Webhook dead-letter count spikes.
- Celery queue depth sustained high.
- Runtime webhook dead-letter alarm is backed by the application custom metrics namespace and should page even when infrastructure metrics look healthy.

Alarm routing:
- CloudWatch Alarm -> SNS -> on-call mailbox / chat integration.

## Logging Requirements
- Include `request_id`, `user_id` (when available), endpoint, status, duration.
- Propagate `X-Request-ID` and `X-Correlation-ID` from ingress to API responses for caller-side correlation.
- Preserve the same correlation ID when API handlers enqueue Celery work and include the parent Celery task ID on worker-to-worker hops so later logs can be traced back to the originating HTTP request and intermediary task.
- Include job IDs for optimization and webhook tasks.
- Capture external provider response code and normalized failure reason.
- Ensure sensitive fields are redacted (`Authorization`, API keys, raw secrets).

## SLO / KPI Mapping
- API p95 latency target mapped to ALB and app metrics.
- Delivery completion and failed-delivery rates mapped to analytics jobs.
- Webhook success KPI mapped to delivery logs and retry/dead-letter metrics.

## Operational Checks
- Verify alarm actions are attached and tested in staging.
- Validate required alarms using `infra/scripts/validate_required_alarms.sh`.
- Run monthly alert fire drills for API failure and queue backlog scenarios.
- Validate dashboard widgets after each infrastructure update.
- Keep retention and sampling policies documented and versioned.
- Confirm ECS task definitions still set `EMF_METRICS_ENABLED=true` and correct `EMF_METRICS_SERVICE` labels after deployment changes.

Runbooks:
- [alarm_fire_drills.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/alarm_fire_drills.md)
- [alarm_fire_drills_execution_log.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/alarm_fire_drills_execution_log.md)
