# Traffic Profiles Runbook

## Purpose
Operational notes for traffic-aware duration multipliers and dispatcher route-geometry caches.

## Runtime metrics
- Endpoint: `GET /api/v1/analytics/runtime-performance` (admin/dispatcher auth).
- Key fields:
  - `dispatch_batch_latency_ms` / `dispatch_on_demand_latency_ms`
  - `eta_compute_latency_ms` / `eta_error_delta_ms`
  - `osrm.osrm_table_success_count` / `osrm.osrm_table_error_count`

## Traffic profile controls
- Feature flags:
  - `TRAFFIC_PROFILE_ENABLED`
  - `ENABLE_TRAFFIC_PROFILES`
- Guardrails:
  - `TRAFFIC_PROFILE_MIN_SAMPLES`
  - `TRAFFIC_PROFILE_EMA_ALPHA`
  - `TRAFFIC_PROFILE_MIN_MULTIPLIER`
  - `TRAFFIC_PROFILE_MAX_MULTIPLIER`

## Reset traffic profile data
Use when profile quality is degraded by bad observations.

```sql
TRUNCATE TABLE traffic_cell_profiles;
```

After truncate:
1. Restart API workers to clear in-memory lookup cache.
2. Observe `avg_traffic_multiplier` values returning close to `1.0` until new samples accumulate.

## Route geometry cache invalidation
Route geometry cache is key-scoped to route status + stop snapshot and stored in Redis.

- TTL is controlled by `ROUTE_GEOMETRY_CACHE_TTL_SECONDS`.
- To force-clear immediately:

```bash
redis-cli --scan --pattern 'route_geometry:*' | xargs -r redis-cli del
```

## Degradation behavior
- If OSRM matrix/route calls fail, dispatch and ETA continue with haversine fallback.
- If traffic-profile lookups fail, system uses baseline OSRM durations.
