# Technical Demo Deep Dive

This document is source-grounded. It describes what the repository actually implements as of 2026-04-29 and is meant to support a technical demo, solution walkthrough, or diligence conversation.

For the direct algorithm, assignment, weighting, and success-metrics explanation, use [Routing and Assignment Fundamentals](routing_and_assignment_fundamentals.md). That document is now the canonical source for questions like "is this Dijkstra?" and "how do we decide which courier gets the order?"

The short version is:

- The optimization core is operations research, not a black-box AI planner.
- Scheduled and bulk planning is solved with Google's OR-Tools vehicle-routing solver. In plain language, it searches for a good courier assignment and stop sequence while obeying constraints.
- Real-time on-demand dispatch uses a deterministic cheapest-insertion heuristic, not the full batch solver.
- Traffic and ETA improvements are learned with lightweight statistical adaptation, not a deep learning model.

## 1. System Surfaces

The product is implemented as four cooperating surfaces:

- Dispatcher web app (`frontend/src`): live operations, order management, fleet, insights, and settings. Main routes live in `frontend/src/App.tsx`.
- Courier mobile app (`mobile/src`): login, route execution, stop detail, loading checklist, barcode/POD capture, offline queue, and live location tracking.
- Customer tracking app (`tracking/src`): no-account tracking page backed by signed tracking links.
- Backend + workers (`backend/app`): FastAPI API, Celery-style background tasks, optimization, tracking, notifications, webhooks, and policy enforcement.

At the architecture level, the backend is a modular monolith, not microservices (`docs/adr/0001-modular-monolith.md`).

### 1.1 Dispatcher Web Capabilities

The dispatcher UI is not just CRUD around orders. The code supports:

- LiveOps map with courier positions, route geometries, route progress, and next-stop context (`frontend/src/pages/LiveOpsPage.tsx`)
- route editing, stop reordering, and dispatcher notes
- optimization preview overlay with draft-route review, explainability, cost delta, and approve/reject flow
- exception center for SLA alerts, failed orders, and optimization-run attention items
- order intake by form and CSV import preview/publish (`frontend/src/pages/OrdersPage.tsx`)
- manual assign, unassign, cancel, reschedule, and "dispatch now" actions on orders
- package and inventory-event visibility from the order detail pane
- fleet management for courier capacity, vehicle type, depot, and working hours (`frontend/src/pages/FleetPage.tsx`)
- insights for delivery, failure, on-time, and courier performance trends (`frontend/src/pages/InsightsPage.tsx`)
- admin settings for operations policy, depots, couriers, constraints, API keys, webhooks, users, and audit log (`frontend/src/pages/SettingsPage.tsx`)

### 1.2 Courier Mobile Capabilities

The courier app supports a full route-execution flow:

- route overview with next stop, progress, status changes, and queue sync health (`mobile/src/screens/MyRouteScreen.tsx`)
- loading checklist before route start with barcode confirmation (`mobile/src/screens/LoadingChecklistScreen.tsx`)
- stop detail flow with arrival scan gating, pre-arrival call guidance, wait timers, and policy-aware proof-of-delivery (`mobile/src/screens/StopDetailScreen.tsx`)
- barcode scan for arrival confirmation
- photo capture and gallery attachment for POD
- signature capture via a signature canvas
- OTP and receiver-name capture when policy requires them
- offline queueing for arrive, complete, and fail actions

### 1.3 Customer Tracking Capabilities

The tracking experience is intentionally lightweight:

- no account creation
- white-labeled branding per tenant
- ETA visibility controlled by workspace policy
- optional live map when policy allows it
- policy-gated self-service rescheduling and instruction updates after failed delivery

## 2. Capability Map

| Problem | Implementation | Where |
| --- | --- | --- |
| Batch planning | Google OR-Tools vehicle-routing solver, called `RoutingModel` in code, solving pickup-and-delivery assignment with capacity and time windows | `backend/app/algorithms/vrp_solver.py` |
| Large-batch decomposition | O(n)-style grid-based geographic clustering with vehicle-to-cluster assignment | `backend/app/algorithms/geo_clustering.py` |
| Real-time on-demand assignment | Deterministic cheapest-insertion heuristic | `backend/app/algorithms/insertion_heuristic.py` |
| Solver warm start | Previous order-to-courier assignment cache reused when overlap is high enough | `backend/app/algorithms/warm_start_cache.py` |
| Post-solve improvement | Intra-route 2-opt local search | `backend/app/algorithms/two_opt.py` |
| Solver fallback | Greedy nearest-neighbor / nearest-insertion fallback | `backend/app/algorithms/greedy_fallback.py` |
| Road-network times and geometry | Self-hosted OSRM route/table/nearest APIs | `backend/app/algorithms/osrm_client.py` |
| Traffic adaptation | Geohash6 + weekday/hour traffic multipliers updated with EMA | `backend/app/services/traffic_profile_service.py` |
| ETA prediction | Remaining-route ETA, then direct-route fallback, then historical bias correction | `backend/app/services/eta_service.py` |
| Auto-arrival / dwell monitoring | Haversine geofence + Redis-backed dwell state | `backend/app/services/geofencing_service.py` |
| Draft route preview | Diff against committed routes with changed orders, violations, and cost delta | `backend/app/services/optimization_run_service.py` |
| Mobile offline resilience | SQLite action queue with exponential retry and conflict detection | `mobile/src/services/offlineQueue.ts` |
| Customer self-service | Signed tracking JWTs with policy-gated reschedule and instruction updates | `backend/app/services/tracking_service.py` |

## 3. Core Domain Model

The main entities are explicit and fairly complete for a last-mile system:

- Orders: `Order`, `OrderStatus`, `OrderType`, `OrderPriority`
- Couriers and fleet: `Courier`, vehicle capabilities, availability, depot position
- Planned execution: `Route`, `RouteStop`, `StopType`, `StopStatus`
- Package inventory: `Package`, inventory events and package states
- Optimization lifecycle: `OptimizationRun`
- Exceptions: `SLAAlert`, failed-order escalation state
- Integrations: `WebhookEndpoint`, `WebhookDeliveryLog`, `ApiClient`
- Governance: `Tenant`, `AuditLog`, RBAC and operations policy

Supported order types are:

- `ON_DEMAND`
- `SCHEDULED`
- `BULK`
- `REVERSE`

Supported vehicle classes are:

- `MOTORCYCLE`
- `CAR_VAN`
- `TRUCK`

Supported stop types are:

- `PICKUP`
- `DELIVERY`
- `DEPOT_RETURN`
- `DEPOT_RELOAD`

## 4. Order Intake, Policies, and State Management

Order creation is not just a plain insert. `backend/app/services/order_service.py:create_order()` does several things in one flow:

- Validates standard and custom fields against the workspace operations policy.
- Enforces dedupe rules around `external_ref`.
- Generates a customer tracking token with `create_tracking_token()`.
- Generates a 6-digit delivery OTP when the POD policy for that order type requires OTP.
- Automatically creates the package/inventory record.
- Optionally auto-enqueues on-demand dispatch if the order type is `ON_DEMAND` and policy mode is automatic or hybrid.

The order state machine is explicit. State transitions are validated through `validate_state_transition()` and applied through `apply_order_status_transition()`, rather than being loosely updated throughout the codebase.

The intake layer also includes bulk import and geocoding support:

- CSV import is preview-first, not blind publish (`backend/app/services/import_service.py`)
- distinct addresses are geocoded concurrently before publish
- the geocoder is Photon-based and biased toward Istanbul (`backend/app/algorithms/photon_client.py`)
- valid rows are published into draft or pending orders while row-level validation errors are retained for invalid rows

Package inventory is part of the main flow, not bolted on later:

- packages are auto-created for orders
- labels are generated in the format `PKG-YYYYMMDD-XXXXXX`
- inventory events drive package state such as depot, loaded, in-transit, and delivered

The operations policy layer is central to the product:

- Industry presets: `parcel`, `food`, `pharmacy`, `b2b`, `returns`
- Optimization presets: `COST_SAVER`, `BALANCED`, `SERVICE_FIRST`
- POD settings: photo count, signature, OTP, receiver name, notes
- Failure handling: retry timing, escalation hours, post-failure action
- Notifications: channel toggles, quiet hours, ETA visibility
- Dispatch settings: mode, assignment strategy, route approval rules
- Team permissions: per-role capability overrides

Important preset penalty defaults:

- `COST_SAVER`: lateness `450/min`, overtime `36/min`, unserved `220,000`
- `BALANCED`: lateness `750/min`, overtime `60/min`, unserved `320,000`
- `SERVICE_FIRST`: lateness `1200/min`, overtime `90/min`, unserved `450,000`

## 5. Routing and Assignment Summary

The full, non-hand-wavy explanation now lives in [Routing and Assignment Fundamentals](routing_and_assignment_fundamentals.md). The demo-safe summary is:

- Road paths and travel-time matrices come from self-hosted OSRM, the Open Source Routing Machine. The OSRM services run with `--algorithm mld`, meaning Multi-Level Dijkstra over prepared road graphs. The application does not implement Dijkstra or A* itself.
- Batch dispatch uses Google's OR-Tools vehicle-routing solver, named `RoutingModel` in code, to assign orders to couriers and sequence stops as a pickup-and-delivery vehicle-routing problem with capacity and time windows.
- On-demand dispatch uses a deterministic cheapest-insertion heuristic, which tries feasible pickup and delivery insertion positions and chooses the lowest weighted detour.
- If OR-Tools cannot return a batch solution, the fallback is greedy nearest-feasible insertion.
- Large batches may be split with grid-based geographic clustering, then solved cluster by cluster.
- Multi-trip planning expands one courier into trip-specific virtual vehicles, then stitches solved trips back into one physical route.
- After a successful batch solve, a 2-opt pass can shorten individual routes without moving orders between couriers.

The main batch entrypoint is `backend/app/services/dispatch_service.py:dispatch_batch()`. The main on-demand entrypoint is `backend/app/services/dispatch_service.py:dispatch_on_demand()`.

Batch eligibility rules:

- Default batch mode considers pending `SCHEDULED`, `BULK`, `PICKUP_AND_DELIVERY`, and `RETURN` orders inside the planning horizon.
- Date-scoped mode includes all order types with `earliest_delivery < date_to` and `latest_delivery >= date_from`.
- Active couriers are `ONLINE` plus `ON_BREAK`, because planning is forward-looking.
- The current batch query limits order fetches to 1000 records per tenant for memory protection.

Current solver time limits:

- Economic-mode batch runs use adaptive limits: `<= 10` orders gets 8 seconds, `<= 30` gets 20 seconds, `<= 80` gets 60 seconds, `<= 200` gets 90 seconds, and larger batches get 150 seconds.
- Legacy-mode batch runs pass no adaptive override, so the OR-Tools solver uses the configured default `TIME_LIMIT_SECONDS`, currently 60 seconds.

Important limits:

- OSRM is a hard dependency for planning matrices and road geometry. Current planning does not silently fall back to straight-line routes if OSRM is down.
- `ZONE_BASED` exists as a policy value, but current dispatch logic does not implement a full separate zone-based optimizer branch.
- The learned traffic component is a lightweight geohash and hour-bucket multiplier model, not a deep learning model.

## 6. Preview, Explainability, and Approval

The system does not force every optimization run straight into committed routes.

`backend/app/services/optimization_run_service.py` can build a draft preview with:

- changed orders marked as `ASSIGNED`, `MOVED`, `UNASSIGNED`, or `REORDERED`
- route-level summary text
- run-level summary text
- cost delta between draft and current plan
- route and run violation lists
- ETA summary and on-time rate
- explainability messages for why the draft changed the plan

This gives the dispatcher a reviewable diff, not just a new route blob.

## 7. Execution on the Road

Execution behavior is handled mainly by `backend/app/services/route_execution_service.py` and `backend/app/services/route_service.py`.

### 7.1 Route Start and Re-Timing

`retime_route_stops()` recomputes planned arrivals using:

- a route anchor from depot or live courier location
- OSRM travel times
- traffic profile multipliers
- configured service time per stop

This is used after route edits and reoptimization triggers.

### 7.2 Arrival

`arrive_at_stop()`:

- enforces sequence order
- marks the stop `ARRIVED`
- records a traffic observation for learning
- can trigger late-stop reoptimization logic
- moves an order from `PICKED_UP` to `IN_TRANSIT` when the courier reaches a delivery stop

### 7.3 Completion and Proof of Delivery

`complete_stop()` enforces policy-driven POD requirements:

- photo count
- signature
- OTP
- receiver name
- notes

It supports role-based override when the policy allows it.

It also drives order-state transitions:

- pickup completion moves the order to `PICKED_UP`
- delivery completion moves it to `DELIVERED`
- `IN_TRANSIT` is applied when appropriate before final delivery

Inventory events are emitted on pickup, reload, and delivery.

### 7.4 Failure Handling

`fail_stop()` does more than mark a stop failed:

- validates the failure reason against policy
- stores custom failure detail if allowed
- marks the order as `FAILED`
- starts a customer escalation clock
- increments attempt count
- either retries, returns to depot, or cancels after max attempts depending on policy

This is a full failure workflow, not just an exception flag.

### 7.5 Route Geometry for LiveOps

`backend/app/services/route_service.py:get_route_geometry()` builds road-following map geometry leg by leg:

- starts from the courier depot
- calls OSRM GeoJSON routing for each consecutive leg
- returns both a full-route feature and per-leg features
- caches the payload in Redis using a route fingerprint

On the frontend, `frontend/src/hooks/useRouteGeometries.ts` and `frontend/src/components/CoordinateMap.tsx` consume this geometry and can fall back to straight polylines when road geometry is unavailable.

## 8. ETA, Tracking, and Geofencing

### 8.1 Customer Tracking

Tracking is based on signed tokens, not customer accounts (`docs/adr/0004-jwt-tracking-links.md`).

The token model is specific:

- one token is scoped to one order
- tracking tokens are issued for 48 hours
- tracking JWTs use a separate issuer, audience, and key path from staff auth tokens

`backend/app/services/tracking_service.py`:

- decodes the tracking JWT
- loads order and tenant branding
- loads latest courier location from cache
- computes ETA if the order is still active
- exposes policy-gated reschedule and instruction updates

ETA visibility modes are:

- `NONE`
- `ETA_RANGE`
- `LIVE_MAP`

### 8.2 ETA Pipeline

ETA logic lives in `backend/app/services/eta_service.py`.

The computation order is:

1. If the order is on a route, compute ETA along the remaining route slice from courier position to target stop.
2. Adjust that route-slice matrix with traffic profile multipliers.
3. If route-slice computation fails, compute a direct OSRM route from courier to customer.
4. Adjust the direct route with traffic profile multipliers if available.
5. If enough historical data exists, apply a historical delay correction using completed deliveries of the same order type and nearby hour bucket.

This is the most accurate demo description:

- "ETA is route-aware when we have route context, road-network-aware through OSRM, traffic-adjusted through learned multipliers, and bias-corrected using historical delay samples."

### 8.3 Geofencing

Geofencing is implemented in `backend/app/services/geofencing_service.py`.

The algorithm is a straightforward radius check using haversine distance:

- arrival radius varies by order type
- current stop is the first pending or arrived stop on the active route
- entering the radius can auto-mark the stop as arrived
- leaving after arrival can trigger a dwell alert after a timeout

Redis is used to remember:

- first exit time
- whether an alert already fired
- last inside timestamp

Geofence events emit:

- webhook tasks
- notification tasks
- audit log entries

## 9. Mobile App: Execution, Location, and Offline Resilience

The courier app is more than a thin UI shell.

### 9.1 Live Location Tracking

`mobile/src/services/locationService.ts`:

- uploads location every 30 seconds or 25 meters
- rejects low-quality samples with accuracy worse than 100 meters
- rejects stale samples older than 120 seconds
- prefers background updates
- falls back to a foreground watcher when background permission is not available

### 9.2 Offline Queue

`mobile/src/services/offlineQueue.ts` stores execution actions in SQLite:

- `ARRIVE_STOP`
- `COMPLETE_STOP`
- `FAIL_STOP`

Replay behavior:

- exponential retry with base 30 seconds and max 15 minutes
- idempotent-state errors are treated as success
- conflict-class API errors are surfaced as conflicts and removed from the queue
- payload issues that cannot succeed are treated as non-retriable

This is a real offline replay mechanism, not just "retry later" toasts.

## 10. Notifications, Webhooks, and Integrations

### 10.1 Notifications

`backend/app/services/notification_service.py` supports:

- email via SES
- SMS via Twilio
- push via Expo or FCM HTTP v1

Important mechanics:

- dedupe key is `sha256(channel|event|recipient|order)`
- email and SMS can be suppressed during quiet hours
- notification state is persisted as `PENDING`, `SENT`, `FAILED`, or `SUPPRESSED`

### 10.2 Webhooks

`backend/app/services/webhook_service.py` implements:

- per-endpoint encrypted secrets
- HMAC-SHA256 signing with header `X-Webhook-Signature`
- structured delivery logs
- replay of dead-letter deliveries
- exponential retry backoff

Backoff is:

```text
delay = min(base_seconds * 2^(attempt_number - 1), max_seconds)
```

Delivery status is explicit:

- `SUCCESS`
- `RETRY_SCHEDULED`
- `DEAD_LETTER`

## 11. Security, Access, and Enterprise Controls

Security features are fairly mature for this stage:

- access and refresh JWTs include issuer, audience, type, JTI, and `kid`
- tracking links use a separate token path and key ring
- revoked-token support is present
- OIDC supports discovery, token exchange, JWKS verification, JIT provisioning, and role sync
- RBAC supports both coarse roles and tenant-level overrides
- email verification supports token-based verification plus a grace-period enforcement model
- API keys are shown once and stored hashed, with a lookup prefix stored separately

## 12. Observability, CI, and Infrastructure

### 12.1 Observability

The backend includes:

- performance counters and latency histograms
- slow-query logging
- N+1 query-shape detection keyed by request ID
- optimization and OSRM metrics
- webhook metrics

### 12.2 API Contract Discipline

The shared contract is generated from backend OpenAPI:

- `backend/scripts/export_openapi_schema.py`
- `shared/openapi.generated.json`
- `shared/api-schema.generated.ts`
- generation uses `openapi-typescript`

### 12.3 CI

The repo has separate reusable backend and web/mobile pipelines.

Backend CI currently runs:

- Ruff
- Alembic migration smoke
- bytecode compile check
- live infrastructure tests
- remaining tests with coverage
- `pip-audit`
- API contract validation

Web/mobile CI currently runs:

- TypeScript type-check
- tests with coverage
- builds for `frontend` and `tracking`
- mobile type-check and tests

Current test module counts in the repo:

- backend: 90 Python test modules
- frontend: 16 test files
- mobile: 11 test files
- tracking: 3 test files

### 12.4 Infrastructure

Terraform under `infra/terraform/aws` provisions the AWS stack:

- VPC and subnets
- ALB
- ECS Fargate for API and workers
- CodeDeploy blue/green deployment
- Postgres on RDS
- Redis on ElastiCache
- S3
- ECR
- CloudWatch
- budgets and finops controls

## 13. Demo-Safe Language

These are accurate ways to describe the product in a technical demo.

Say this:

- "Scheduled planning uses Google's OR-Tools vehicle-routing solver to assign orders to couriers and choose stop order under capacity and time-window constraints."
- "Large planning waves are geographically decomposed with a grid-based clustering heuristic before solving."
- "Urgent on-demand jobs are inserted with a deterministic cheapest-insertion heuristic rather than re-solving the whole fleet every time."
- "Routing and ETA calculations use a self-hosted OSRM road graph; OSRM means Open Source Routing Machine."
- "Traffic adaptation is implemented as geohash and time-bucket multipliers updated from observed versus planned travel time."
- "ETAs are route-aware when route context exists and fall back to direct routing when it does not."
- "The system supports draft optimization runs and explainable diffs before committing a route plan."

Avoid saying this:

- "We use AI to decide routes."
- "We have a generic routing solver."
- "We always do zone-based assignment."
- "We fall back to straight-line routing if mapping is unavailable."
- "Customers authenticate into tracking."

## 14. Honest Boundaries and Current Limits

These are important for staying technically accurate:

- OSRM is a hard dependency for route and matrix generation. Failures are loud.
- `ZONE_BASED` exists as a policy enum, but it should not be presented as a separately implemented dispatch algorithm branch.
- The learned parts of the system are lightweight adaptive statistics, not a full ML model.
- Batch dispatch currently protects itself with a 1000-order fetch cap per tenant.
- Multi-trip is skipped in date-scoped batch mode.

## 15. Best One-Paragraph Technical Summary

If we need one dense but accurate summary in the demo, this is the cleanest version:

This platform is a modular-monolith last-mile system with a dispatcher web app, courier mobile app, customer tracking app, and a FastAPI plus worker backend. Scheduled and bulk planning use Google's OR-Tools vehicle-routing solver to assign orders to couriers and choose stop order under explicit constraints for time windows, capacity, breaks, fragile or temperature-sensitive freight, max distance, max stops, optional fleet balancing, multi-trip expansion, warm starts, and a post-solve 2-opt pass. Real-time on-demand jobs use a separate deterministic cheapest-insertion heuristic. Road paths, road travel times, and map geometry come from self-hosted OSRM, the Open Source Routing Machine, then ETAs are adjusted with geohash and time-bucket traffic multipliers learned from observed travel times. The execution layer adds geofenced auto-arrival, proof-of-delivery enforcement, failed-delivery recovery workflows, draft route preview and explainability, customer self-service tracking links, and integrations through signed webhooks and multi-channel notifications.
