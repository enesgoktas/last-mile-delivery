"""Post-process VRP solutions to inject charging stops for EV routes.

This is the implementation that backs ``ev_auto_charging_enabled``
end-to-end. The pre-solver infrastructure (candidate-charger lookup,
synthetic OrderData injection, persistence translation to
``StopType.CHARGING``) has been in place for a while, but the OR-Tools
kernel never actually treats the synthetic charger orders as refuel
events — they're optional pickup+delivery pairs the solver tends to
skip. As a result EVs that need a mid-shift charge simply have their
range-exceeding routes dropped.

This module patches that gap by walking each returned ``PlannedRoute``
and inserting one or more charging stops where the running battery
state-of-charge approaches empty. The inserted stops follow the existing
``__CHARGER_<station>_<courier>__`` synthetic order-id convention that
``_persist_solution`` already understands.

**Distances**. Uses the same OSRM-derived ``distance_matrix`` the solver
was fed (in metres). Falls back to Haversine × 1.3 only when a point
isn't in ``loc_index`` (shouldn't happen — every route stop and every
candidate charger is added to ``locations`` upstream — but the fallback
keeps the post-processor robust against rounding drift).

**Charge duration**. Per-insertion call to
``charging_station_service.estimate_charge_time_seconds(...)`` using the
EV's actual ``battery_kwh`` and the station's ``max_kw``. So a Tesla
Model 3 (75 kWh) topping up from 15% to 80% at a 50 kW DC fast charger
gets ~58 min × 1.1 taper = ~64 min, not a flat 30 min.

**RIDE interlock**. Charging while passengers are aboard is operationally
illegal in every fleet we've seen. The post-processor tracks passenger
load across the route and refuses to insert a charger at any position
where load > 0; if the natural insertion point is mid-RIDE it backtracks
to the most-recent zero-load position. If no zero-load position exists
earlier, the route is left alone and a warning is logged.
"""
from __future__ import annotations

import logging
import math
from dataclasses import replace
from typing import Mapping

from app.algorithms.vrp_solver import PlannedRoute, PlannedStop, VRPSolution
from app.models.charging_station import ChargingStation
from app.models.enums import OrderType, StopType
from app.models.order import Order
from app.models.vehicle_spec import VehicleSpec
from app.services.charging_station_service import estimate_charge_time_seconds

logger = logging.getLogger(__name__)


# Safety margin: trigger the charger search when 85% of the remaining
# range has been consumed. Leaves ~50-80 km of slack on a typical EV —
# enough to reach a nearby charger when one isn't directly on path.
_DEFAULT_SAFETY_THRESHOLD = 0.85

# Target state-of-charge after a mid-route stop. Fast-charging curves
# taper above 80%; we plan to top up to here only.
_DEFAULT_TARGET_PCT = 80

# Fallback charger power for stations with unknown max_kw (some OSM rows
# have no socket:output data). 22 kW is the Turkish public-charger AC
# default.
_FALLBACK_CHARGER_KW = 22

# Fallback urban driving speed for time-shifting subsequent stops when
# we insert a charger. The OSRM duration matrix would give a precise
# value, but converting at 60 s/km is close enough for the time-window
# bookkeeping (the solver already validated the original arrivals).
_URBAN_TRAVEL_SECONDS_PER_KM = 60


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    earth_radius_m = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return int(earth_radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


class _DistanceLookup:
    """Wraps the OSRM ``distance_matrix`` and ``loc_index`` from dispatch.

    Returns metres for any (lat, lon, lat, lon) pair. When either point
    isn't in ``loc_index`` (shouldn't happen given how the dispatch flow
    pre-includes charger coordinates), falls back to Haversine × 1.3.
    """

    __slots__ = ("_matrix", "_index", "_fallback_count")

    def __init__(
        self,
        distance_matrix: list[list[int]] | None,
        loc_index: Mapping[tuple[float, float], int] | None,
    ) -> None:
        self._matrix = distance_matrix
        self._index = loc_index
        self._fallback_count = 0

    def get(self, lat1: float, lon1: float, lat2: float, lon2: float) -> int:
        if self._matrix is not None and self._index is not None:
            key_a = (round(lat1, 5), round(lon1, 5))
            key_b = (round(lat2, 5), round(lon2, 5))
            ia = self._index.get(key_a)
            ib = self._index.get(key_b)
            if ia is not None and ib is not None:
                try:
                    return int(self._matrix[ia][ib])
                except (IndexError, TypeError):
                    pass
        # Off-matrix: warn once per processor instance so we surface drift
        # without spamming the log.
        if self._fallback_count == 0:
            logger.warning(
                "charging_post_processor: coord not in OSRM loc_index; "
                "falling back to Haversine × 1.3 for this segment "
                "(lat1=%.5f lon1=%.5f → lat2=%.5f lon2=%.5f)",
                lat1, lon1, lat2, lon2,
            )
        self._fallback_count += 1
        return int(_haversine_m(lat1, lon1, lat2, lon2) * 1.3)


def _compute_passenger_load_profile(
    stops: list[PlannedStop],
    orders_by_id: Mapping[str, Order],
) -> list[int]:
    """For each i, return passengers ALREADY aboard when arriving at
    ``stops[i]`` (i.e. before processing this stop's own pickup or
    delivery). A charger may only be inserted at positions where this is
    zero."""
    profile = [0] * len(stops)
    aboard = 0
    for i, stop in enumerate(stops):
        profile[i] = aboard
        if not stop.order_id or stop.order_id.startswith("__CHARGER_"):
            continue
        order = orders_by_id.get(stop.order_id)
        if order is None or order.type != OrderType.RIDE:
            continue
        passenger_count = int(getattr(order, "passenger_count", 1) or 1)
        if stop.stop_type == StopType.PICKUP.value:
            aboard += passenger_count
        elif stop.stop_type == StopType.DELIVERY.value:
            aboard -= passenger_count
    return profile


# ── Reachability / operating-hours / contention guards ──────────────────


# How much of remaining_m we'll burn to reach a charger. 0.95 leaves
# 5% buffer for the OSRM estimate being slightly optimistic or for
# real-world losses we haven't modelled (HVAC, traffic, driving style).
_REACHABILITY_BUFFER = 0.95


def _is_station_open_at(station: ChargingStation, arrival_time_s: int) -> bool:
    """Is the station open at the given seconds-from-midnight time?

    Fast path: ``is_24_7=True`` → always yes.

    Otherwise inspect ``operating_hours`` (free-form JSONB). We support
    two simple shapes:
      ``{"open_s": <int>, "close_s": <int>}`` — daily window in
        seconds-from-midnight. ``close_s < open_s`` means wrap around
        midnight (e.g. 22:00 → 06:00).
      ``{"closed": true}`` — explicitly closed.

    Unknown / unparseable shapes → assume open (conservative; we'd
    rather over-include than strand the route).
    """
    if getattr(station, "is_24_7", True):
        return True
    hours = getattr(station, "operating_hours", None)
    if not isinstance(hours, dict):
        return True
    if hours.get("closed") is True:
        return False
    try:
        open_s = int(hours["open_s"])
        close_s = int(hours["close_s"])
    except (KeyError, TypeError, ValueError):
        return True
    if close_s >= open_s:
        return open_s <= arrival_time_s <= close_s
    # Wraps midnight: open from open_s until 24h, plus 0 until close_s.
    return arrival_time_s >= open_s or arrival_time_s <= close_s


def _pick_best_charger(
    anchor: tuple[float, float],
    next_stop: tuple[float, float],
    candidates: list[ChargingStation],
    distances: _DistanceLookup,
    *,
    remaining_m: int,
    anchor_dep_s: int,
    contention: dict[tuple[str, int], int],
    max_per_station_per_hour: int,
) -> tuple[ChargingStation | None, int, str | None]:
    """Pick the lowest-detour charger that's actually safe to route to.

    Returns ``(station, detour_metres, refusal_reason)``. ``station`` is
    None when every candidate fails one of the guards; in that case
    ``refusal_reason`` is the dominant failure for the warning log.

    Guards, applied in order:
      1. **Reachability**: ``distance(anchor → station) > remaining_m *
         0.95`` — the EV physically can't reach the station with the
         charge left. Caused stranding in earlier versions.
      2. **Operating hours**: station closed at the time we'd arrive.
         24/7 stations skip this check.
      3. **Contention**: another EV in this solution is already routed
         through ``(station, arrival_hour)``; ``max_per_station_per_hour``
         caps how many we'll plan to share a station per hour.
    """
    if not candidates:
        return None, 0, "no_candidates"

    direct_m = distances.get(anchor[0], anchor[1], next_stop[0], next_stop[1])
    reach_limit = int(remaining_m * _REACHABILITY_BUFFER)
    rejected: dict[str, int] = {"unreachable": 0, "closed": 0, "contended": 0}
    best: ChargingStation | None = None
    best_detour = math.inf

    for station in candidates:
        leg_to = distances.get(anchor[0], anchor[1], station.lat, station.lon)
        if leg_to > reach_limit:
            rejected["unreachable"] += 1
            continue

        arrival_s = anchor_dep_s + (leg_to // 1000) * _URBAN_TRAVEL_SECONDS_PER_KM
        if not _is_station_open_at(station, arrival_s % 86_400):
            rejected["closed"] += 1
            continue

        hour_bucket = arrival_s // 3600
        key = (str(station.id), hour_bucket)
        if contention.get(key, 0) >= max_per_station_per_hour:
            rejected["contended"] += 1
            continue

        leg_from = distances.get(station.lat, station.lon, next_stop[0], next_stop[1])
        detour = (leg_to + leg_from) - direct_m
        if detour < best_detour:
            best_detour = detour
            best = station

    if best is None:
        # Pick the dominant rejection so the warning is informative.
        reason = max(rejected, key=rejected.get) if any(rejected.values()) else "no_candidates"
        return None, 0, reason
    return best, int(best_detour), None


def _resolve_charge_time_s(
    station: ChargingStation,
    remaining_m: int,
    full_range_m: int,
    battery_kwh: float | None,
    target_pct: int,
) -> tuple[int, float]:
    """Call ``estimate_charge_time_seconds`` with the courier's actual
    battery_kwh and the station's max_kw. Falls back to a 60-min default
    only when battery_kwh is missing (legacy VehicleSpec without the EV
    fields populated).

    Returns ``(charge_seconds, kwh_delivered)``. ``kwh_delivered`` is 0
    when ``battery_kwh`` is unknown so callers (cost breakdown) won't
    overstate kWh on couriers whose spec lacks the field.
    """
    current_pct = max(0, min(100, int(remaining_m * 100 / max(full_range_m, 1))))
    charger_kw = float(getattr(station, "max_kw", 0) or 0) or _FALLBACK_CHARGER_KW
    if not battery_kwh or battery_kwh <= 0:
        # No EV-spec battery_kwh on this courier's vehicle_spec. Best we
        # can do is a flat fallback so the route still gets a charging
        # stop. A real fleet should populate battery_kwh on VehicleSpec.
        logger.warning(
            "charging_post_processor: vehicle_spec.battery_kwh is missing "
            "for the courier on this route; using 60-min charge fallback"
        )
        return 60 * 60, 0.0
    charge_s = estimate_charge_time_seconds(
        current_pct=current_pct,
        target_pct=target_pct,
        battery_kwh=float(battery_kwh),
        charger_kw=charger_kw,
    )
    kwh_delivered = max(0.0, (target_pct - current_pct) / 100.0 * float(battery_kwh))
    return charge_s, kwh_delivered


def _inject_for_route(
    route: PlannedRoute,
    *,
    candidates: list[ChargingStation],
    full_range_m: int,
    initial_charge_m: int,
    battery_kwh: float | None,
    target_pct: int,
    orders_by_id: Mapping[str, Order],
    distances: _DistanceLookup,
    safety_threshold: float,
    contention: dict[tuple[str, int], int],
    max_per_station_per_hour: int,
    electricity_price_try_per_kwh: float | None = None,
) -> PlannedRoute:
    stops = list(route.stops)
    if len(stops) < 2:
        return route

    pax_profile = _compute_passenger_load_profile(stops, orders_by_id)
    # Running totals for the route-level cost breakdown patched at exit.
    # ``kwh_this_route`` stays at 0 (rather than None) until a charger is
    # actually inserted — the caller checks ``len(new_stops) > len(stops)``
    # to decide whether to record the kWh at all.
    kwh_this_route = 0.0

    new_stops: list[PlannedStop] = [stops[0]]
    # Track remaining battery (in metres of range) directly. Far cleaner
    # than a (consumed, budget) pair when there are multiple refills.
    remaining_m = initial_charge_m
    # Defense in depth: if some earlier stage (the pre-solver synthetic
    # injection used to do this) somehow already inserted a charger at
    # the start of the route, the EV arrives at the next real stop with
    # a fresh ``target_pct`` tank rather than the depleted starting
    # charge. Without this we'd double-charge.
    if stops[0].order_id and stops[0].order_id.startswith("__CHARGER_"):
        remaining_m = int(full_range_m * target_pct / 100)
    last_lat, last_lon = stops[0].lat, stops[0].lon

    # Hard cap on insertions per route. Defense in depth: even if a
    # logic bug caused the trigger to fire repeatedly without making
    # progress, we'd cap out instead of looping forever.
    max_insertions_per_route = max(3, 2 + len(stops) // 5)
    insertions_done = 0
    # The maximum range one mid-route charge restores us to. If a single
    # segment exceeds this, no number of chargers can cover it — the EV
    # is fundamentally underpowered for this hop.
    max_post_charge_range_m = int(full_range_m * target_pct / 100)

    i = 1
    while i < len(stops):
        segment_m = distances.get(last_lat, last_lon, stops[i].lat, stops[i].lon)

        if segment_m > remaining_m * safety_threshold:
            # Single-segment infeasibility check. If even a fresh,
            # target_pct-full tank can't cover this leg, no charger will
            # help and re-triggering would loop forever. Refuse and
            # advance.
            if segment_m > max_post_charge_range_m:
                logger.error(
                    "charging_post_processor: route %s has a single segment "
                    "of %.1f km between stop %d and stop %d that exceeds "
                    "even a full %d%%-charged tank (%.1f km). This EV is "
                    "underpowered for this route — leaving the route as-is.",
                    route.courier_id,
                    segment_m / 1000, i - 1, i,
                    target_pct, max_post_charge_range_m / 1000,
                )
                new_stops.append(stops[i])
                remaining_m -= segment_m
                last_lat, last_lon = stops[i].lat, stops[i].lon
                i += 1
                continue

            if insertions_done >= max_insertions_per_route:
                logger.error(
                    "charging_post_processor: route %s hit the per-route "
                    "insertion cap (%d) without finishing — leaving "
                    "remaining stops as-is to avoid an infinite loop.",
                    route.courier_id, max_insertions_per_route,
                )
                # Append remaining stops verbatim and bail.
                for j in range(i, len(stops)):
                    new_stops.append(stops[j])
                break
            # Need to charge before reaching stops[i]. Find latest
            # insertion index <= i where pax_profile[insert_at] == 0.
            insert_at = i
            while insert_at > 0 and pax_profile[insert_at] != 0:
                insert_at -= 1
            if pax_profile[insert_at] != 0:
                logger.warning(
                    "charging_post_processor: route %s would exceed battery "
                    "near stop %d but every prior position has passengers "
                    "aboard — leaving route unchanged from this point on",
                    route.courier_id, i,
                )
                new_stops.append(stops[i])
                remaining_m -= segment_m  # may go negative; that's the dispatcher's signal
                last_lat, last_lon = stops[i].lat, stops[i].lon
                i += 1
                continue

            # Back-track new_stops to insert_at so we re-append after the
            # charger with corrected timing.
            backtrack = i - insert_at
            tail_to_replay: list[PlannedStop] = []
            for _ in range(backtrack):
                tail_to_replay.append(new_stops.pop())
            tail_to_replay.reverse()

            anchor_stop = new_stops[-1] if new_stops else stops[0]
            anchor = (anchor_stop.lat, anchor_stop.lon)
            next_point = (stops[insert_at].lat, stops[insert_at].lon)
            station, _detour_m, refusal = _pick_best_charger(
                anchor, next_point, candidates, distances,
                remaining_m=remaining_m,
                anchor_dep_s=anchor_stop.departure_time_s,
                contention=contention,
                max_per_station_per_hour=max_per_station_per_hour,
            )
            if station is None:
                # No safe charger — restore the popped tail, log loudly,
                # and DON'T plan a stranding. The dispatcher will see the
                # over-range route in the unassigned/at-risk surface.
                logger.error(
                    "charging_post_processor: route %s needs charging near "
                    "stop %d but no candidate is %s — leaving route "
                    "as-is so the dispatcher sees the risk (remaining=%dm, "
                    "candidates=%d)",
                    route.courier_id, i,
                    {
                        "unreachable": "reachable from the current position",
                        "closed": "open at the planned arrival time",
                        "contended": "under the per-hour contention cap",
                        "no_candidates": "available at all",
                    }.get(refusal or "no_candidates", "viable"),
                    remaining_m, len(candidates),
                )
                new_stops.extend(tail_to_replay)
                new_stops.append(stops[i])
                remaining_m -= segment_m
                last_lat, last_lon = stops[i].lat, stops[i].lon
                i += 1
                continue

            # Recompute distances using the real matrix.
            travel_to_charger_m = distances.get(
                anchor[0], anchor[1], station.lat, station.lon
            )
            travel_to_charger_s = (
                travel_to_charger_m // 1000 * _URBAN_TRAVEL_SECONDS_PER_KM
            )

            # Discharge before arriving at the charger. ``remaining_m`` at
            # this point is the budget AT anchor_stop departure. Subtract
            # the leg to the charger so the charge-time math uses the
            # actual depleted state-of-charge.
            remaining_at_charger_m = max(0, remaining_m - travel_to_charger_m)
            charge_s, kwh_delivered = _resolve_charge_time_s(
                station,
                remaining_m=remaining_at_charger_m,
                full_range_m=full_range_m,
                battery_kwh=battery_kwh,
                target_pct=target_pct,
            )
            kwh_this_route += kwh_delivered

            charger_arrival_s = anchor_stop.departure_time_s + travel_to_charger_s
            charger_dep_s = charger_arrival_s + charge_s

            # Bump the solution-wide contention map so subsequent routes
            # (or another charger insertion on the same route) see this
            # station as one slot more occupied at this hour.
            hour_bucket = charger_arrival_s // 3600
            key = (str(station.id), hour_bucket)
            contention[key] = contention.get(key, 0) + 1

            charger_stop = PlannedStop(
                order_id=f"__CHARGER_{station.id}_{route.courier_id}__",
                stop_type=StopType.PICKUP.value,
                location_index=-1,  # not in original solver location list
                lat=station.lat,
                lon=station.lon,
                arrival_time_s=charger_arrival_s,
                departure_time_s=charger_dep_s,
            )
            new_stops.append(charger_stop)
            insertions_done += 1

            # Replay the popped stops with corrected times AND accumulate
            # the path distance from the charger through every replayed
            # leg. The cumulative path is what the battery actually pays
            # for — using only the straight-line charger→last_replay
            # distance under-counts consumption when the tail snakes.
            current_lat, current_lon = station.lat, station.lon
            current_dep_s = charger_dep_s
            path_since_charge_m = 0
            for replay in tail_to_replay:
                leg_m = distances.get(current_lat, current_lon, replay.lat, replay.lon)
                path_since_charge_m += leg_m
                leg_s = leg_m // 1000 * _URBAN_TRAVEL_SECONDS_PER_KM
                arrival = current_dep_s + leg_s
                service = replay.departure_time_s - replay.arrival_time_s
                departure = arrival + service
                new_stops.append(replace(
                    replay,
                    arrival_time_s=arrival,
                    departure_time_s=departure,
                ))
                current_lat, current_lon = replay.lat, replay.lon
                current_dep_s = departure

            # After the charge we have `target_pct` of full range. Subtract
            # the cumulative distance we actually drove since the charger.
            remaining_m = int(full_range_m * target_pct / 100) - path_since_charge_m
            last_lat, last_lon = current_lat, current_lon
            # i stays the same — stops[i] hasn't been appended yet; loop
            # back around to handle it (or insert another charger if
            # remaining_m still won't cover it).
        else:
            new_stops.append(stops[i])
            # Defense in depth: if an upstream stage somehow embedded a
            # ``__CHARGER_*`` stop mid-route, the EV emerges from it with
            # a fresh ``target_pct`` tank rather than the depleted SoC
            # that ``remaining_m -= segment_m`` would imply.
            if stops[i].order_id and stops[i].order_id.startswith("__CHARGER_"):
                remaining_m = int(full_range_m * target_pct / 100)
            else:
                remaining_m -= segment_m
            last_lat, last_lon = stops[i].lat, stops[i].lon
            i += 1

    new_route = replace(route, stops=new_stops)
    # Patch the cost breakdown only when we actually inserted at least one
    # charger. Leaving kWh as ``None`` on untouched routes keeps the
    # solution-level aggregator's "did any route charge?" check honest.
    if len(new_stops) > len(stops) and route.cost_breakdown is not None:
        price = float(electricity_price_try_per_kwh or 0.0)
        charging_cost = int(round(kwh_this_route * price))
        existing = route.cost_breakdown
        new_route = replace(
            new_route,
            cost_breakdown=replace(
                existing,
                estimated_charge_kwh=round(kwh_this_route, 3),
                charging_cost=existing.charging_cost + charging_cost,
                total_generalized_cost=existing.total_generalized_cost + charging_cost,
            ),
        )
    return new_route


def inject_charging_stops_into_solution(
    solution: VRPSolution,
    *,
    candidate_chargers_by_courier: Mapping[str, list[ChargingStation]],
    orders_by_id: Mapping[str, Order],
    full_range_m_by_courier: Mapping[str, int],
    initial_charge_m_by_courier: Mapping[str, int],
    vehicle_spec_by_courier: Mapping[str, VehicleSpec] | None = None,
    distance_matrix: list[list[int]] | None = None,
    loc_index: Mapping[tuple[float, float], int] | None = None,
    safety_threshold: float = _DEFAULT_SAFETY_THRESHOLD,
    target_pct: int = _DEFAULT_TARGET_PCT,
    max_evs_per_station_per_hour: int = 2,
    electricity_price_try_per_kwh: float | None = None,
) -> VRPSolution:
    """Walk every route in ``solution``, inserting charging stops where
    cumulative travel approaches the battery budget for that courier.

    Routes for couriers with no candidate chargers, no battery budget,
    or already short enough are returned unchanged. The returned
    ``VRPSolution`` shares immutable fields with the input where no
    insertions happened.
    """
    distances = _DistanceLookup(distance_matrix, loc_index)
    vehicle_spec_by_courier = vehicle_spec_by_courier or {}

    # Solution-wide map: (station_id_str, arrival_hour) -> count of our
    # own EVs already planned through that station this hour. Each
    # successful insertion bumps the relevant slot so subsequent routes
    # (or another charger on the same route) see the contention.
    contention: dict[tuple[str, int], int] = {}

    new_routes: list[PlannedRoute] = []
    insertions_total = 0
    for route in solution.routes:
        candidates = candidate_chargers_by_courier.get(route.courier_id, [])
        full_range_m = full_range_m_by_courier.get(route.courier_id, 0)
        if not candidates or full_range_m <= 0 or len(route.stops) < 2:
            new_routes.append(route)
            continue
        initial_charge_m = initial_charge_m_by_courier.get(
            route.courier_id, full_range_m
        )
        spec = vehicle_spec_by_courier.get(route.courier_id)
        battery_kwh = float(spec.battery_kwh) if spec and spec.battery_kwh else None

        before_n = len(route.stops)
        new_route = _inject_for_route(
            route,
            candidates=candidates,
            full_range_m=full_range_m,
            initial_charge_m=initial_charge_m,
            battery_kwh=battery_kwh,
            target_pct=target_pct,
            orders_by_id=orders_by_id,
            distances=distances,
            safety_threshold=safety_threshold,
            contention=contention,
            max_per_station_per_hour=max_evs_per_station_per_hour,
            electricity_price_try_per_kwh=electricity_price_try_per_kwh,
        )
        added = len(new_route.stops) - before_n
        insertions_total += max(added, 0)
        new_routes.append(new_route)

    if insertions_total:
        logger.info(
            "charging_post_processor: injected %d charging stop(s) across "
            "%d routes (off-matrix segments: %d)",
            insertions_total, len(solution.routes), distances._fallback_count,
        )

    return replace(solution, routes=new_routes)
