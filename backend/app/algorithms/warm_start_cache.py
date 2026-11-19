"""
Warm-start cache — stores previous VRP solution for seeding next solve.

Caches order-to-vehicle assignments from the last successful batch solve.
On the next solve, these are converted to initial routes if the same
orders and vehicles are present.

Thread-safe via a lock. TTL prevents using stale assignments.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field

import redis

from app.algorithms.vrp_solver import VRPInput, VRPSolution
from app.config import settings
from app.utils.tenant_context import get_current_tenant_scope

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600  # discard cache entries older than 1 hour


@dataclass
class CachedAssignment:
    """Cached mapping from the previous solve."""
    order_to_courier: dict[str, str] = field(default_factory=dict)
    courier_routes: dict[str, list[str]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


class _InProcessCache:
    _cache: dict[str, CachedAssignment] = {}
    _lock = threading.Lock()

    @classmethod
    def set(cls, key: str, assignment: CachedAssignment) -> None:
        with cls._lock:
            cls._cache[key] = assignment

    @classmethod
    def get(cls, key: str) -> CachedAssignment | None:
        with cls._lock:
            return cls._cache.get(key)

    @classmethod
    def delete(cls, key: str) -> None:
        with cls._lock:
            cls._cache.pop(key, None)


_redis_client: redis.Redis | None = None
_redis_lock = threading.Lock()


def _get_redis() -> redis.Redis | None:
    global _redis_client
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                try:
                    client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=1.0)
                    client.ping()
                    _redis_client = client
                except Exception as e:
                    logger.warning("Failed to connect to Redis for warm-start cache: %s. Using in-process fallback.", e)
                    return None
    return _redis_client


# Bumped to v2 in Phase 3 multi-depot — solutions cached before the
# allowed-depot validation could contain RELOAD visits at the home
# depot index, which the new validator still accepts, so v1 entries
# remain semantically valid. The version bump exists to allow a
# blanket cache miss the next time the schema changes in a way that
# might invalidate cached assignments without writing migration code.
_CACHE_SCHEMA_VERSION = 2


def _get_cache_key(tenant_id: str | None = None) -> str:
    if tenant_id is None:
        tenant = get_current_tenant_scope()
        tenant_str = str(tenant) if tenant else "global"
    else:
        tenant_str = tenant_id
    return f"warm_start:v{_CACHE_SCHEMA_VERSION}:{tenant_str}"


def cache_solution(solution: VRPSolution, tenant_id: str | None = None) -> None:
    """Cache assignments from a successful solve for warm-starting."""
    if not solution.routes:
        return

    assignment = CachedAssignment()
    for route in solution.routes:
        order_ids_in_route = []
        for stop in route.stops:
            if stop.stop_type in ("DEPOT_RETURN", "DEPOT_RELOAD"):
                continue
            if stop.stop_type == "PICKUP":
                assignment.order_to_courier[stop.order_id] = route.courier_id
                order_ids_in_route.append(stop.order_id)

        assignment.courier_routes[route.courier_id] = order_ids_in_route

    key = _get_cache_key(tenant_id)
    client = _get_redis()

    if client is not None:
        try:
            data = json.dumps(asdict(assignment))
            client.set(key, data, ex=CACHE_TTL_SECONDS)
        except Exception as e:
            logger.warning("Failed to write warm-start cache to Redis: %s", e)
            _InProcessCache.set(key, assignment)
    else:
        _InProcessCache.set(key, assignment)

    logger.info(
        "Warm-start cache updated: %d orders across %d couriers",
        len(assignment.order_to_courier),
        len(assignment.courier_routes),
    )


def build_initial_routes(
    vrp_input: VRPInput,
    tenant_id: str | None = None,
) -> list[list[int]] | None:
    """Build initial route node sequences from cached assignments.

    Returns None if no cache, stale cache, or insufficient overlap with
    current input.
    """
    key = _get_cache_key(tenant_id)
    client = _get_redis()
    cache_snapshot = None

    if client is not None:
        try:
            data = client.get(key)
            if data:
                raw = json.loads(data)
                cache_snapshot = CachedAssignment(
                    order_to_courier=raw.get("order_to_courier", {}),
                    courier_routes=raw.get("courier_routes", {}),
                    created_at=raw.get("created_at", time.monotonic())
                )
        except Exception as e:
            logger.warning("Failed to read warm-start cache from Redis: %s", e)
            cache_snapshot = _InProcessCache.get(key)
    else:
        cache_snapshot = _InProcessCache.get(key)

    if cache_snapshot is None:
        return None

    # For Redis caching we use EX (TTL) on the key itself, so we only need to
    # check age for the in-process fallback or if TTL somehow missed.
    if time.monotonic() - cache_snapshot.created_at > CACHE_TTL_SECONDS:
        logger.info("Warm-start cache expired (age %.0fs > TTL %ds), skipping",
                     time.monotonic() - cache_snapshot.created_at, CACHE_TTL_SECONDS)
        return None

    orders = vrp_input.orders
    vehicles = vrp_input.vehicles

    # Build lookup: order_id -> OrderData
    order_by_id = {o.order_id: o for o in orders}

    # Build lookup: courier_id -> vehicle index
    courier_to_vidx: dict[str, int] = {}
    for i, v in enumerate(vehicles):
        courier_to_vidx[v.courier_id] = i

    # Virtual-node layout matches `vrp_solver.solve()` exactly: Phase 5
    # gave each vehicle TWO depot slots (start + end), so the order
    # portion of the layout starts at `2 * num_vehicles`. Using the
    # pre-Phase-5 `len(vehicles)` here would point cached pickup vnodes
    # into other vehicles' end-depot slots and OR-Tools would reject
    # the warm-start as malformed.
    n_depots = 2 * len(vehicles)

    # Build order_id -> virtual node indices
    order_to_pickup_vnode: dict[str, int] = {}
    order_to_delivery_vnode: dict[str, int] = {}
    for i, order in enumerate(orders):
        order_to_pickup_vnode[order.order_id] = n_depots + 2 * i
        order_to_delivery_vnode[order.order_id] = n_depots + 2 * i + 1

    # Check overlap — only use cache if enough orders match
    current_order_ids = set(order_by_id.keys())
    cached_order_ids = set(cache_snapshot.order_to_courier.keys())
    overlap = current_order_ids & cached_order_ids
    overlap_ratio = len(overlap) / len(current_order_ids) if current_order_ids else 0

    if overlap_ratio < 0.3:
        logger.info(
            "Warm-start cache: insufficient overlap (%.0f%%), skipping",
            overlap_ratio * 100,
        )
        return None

    # Build initial routes for each vehicle
    initial_routes: list[list[int]] = []
    matched_orders = 0

    for vehicle in vehicles:
        cached_route = cache_snapshot.courier_routes.get(vehicle.courier_id, [])
        vehicle_nodes: list[int] = []

        for order_id in cached_route:
            if order_id not in order_to_pickup_vnode:
                continue  # Order not in current problem
            vehicle_nodes.append(order_to_pickup_vnode[order_id])
            vehicle_nodes.append(order_to_delivery_vnode[order_id])
            matched_orders += 1

        initial_routes.append(vehicle_nodes)

    if matched_orders == 0:
        logger.info("Warm-start cache: no orders matched vehicle assignments, skipping")
        return None

    logger.info(
        "Warm-start cache: seeding %d orders (%.0f%% of current)",
        matched_orders,
        matched_orders / len(orders) * 100 if orders else 0,
    )

    return initial_routes


def clear_cache(tenant_id: str | None = None) -> None:
    """Clear the warm-start cache."""
    key = _get_cache_key(tenant_id)
    client = _get_redis()
    if client is not None:
        try:
            client.delete(key)
        except Exception as e:
            logger.warning("Failed to clear warm-start cache from Redis: %s", e)
    _InProcessCache.delete(key)
