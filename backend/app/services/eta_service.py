from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.algorithms import osrm_client
from app.config import settings
from app.models.enums import OrderStatus, StopStatus, StopType
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.services import traffic_profile_service
from app.services.vehicle_routing_profile import resolve_osrm_profile_from_values
from app.utils.compat import utcnow_naive
from app.utils.perf_metrics import perf_metrics

logger = logging.getLogger(__name__)

TERMINAL_ORDER_STATUSES = {OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.EXPIRED}
TERMINAL_STOP_STATUSES = {StopStatus.COMPLETED, StopStatus.FAILED}


@dataclass(slots=True)
class RouteProgress:
    total_stops: int | None = None
    completed_stops: int | None = None
    remaining_stops: int | None = None
    next_stop_sequence: int | None = None
    target_stop_sequence: int | None = None
    route_slice_to_target: list[RouteStop] | None = None


@dataclass(slots=True)
class EtaComputation:
    estimated_arrival: datetime | None = None
    eta_source: str | None = None
    total_stops: int | None = None
    completed_stops: int | None = None
    remaining_stops: int | None = None
    next_stop_sequence: int | None = None
    target_stop_sequence: int | None = None


def sum_path_duration_seconds(durations: list[list[int]]) -> int:
    """Sum durations from node i to i+1 for a path adjacency matrix."""
    if not durations:
        return 0
    return sum(int(durations[i][i + 1]) for i in range(len(durations) - 1))


def build_route_progress(
    stops: list[RouteStop],
    *,
    order_id: uuid.UUID,
) -> RouteProgress:
    ordered = sorted(stops, key=lambda s: s.sequence_number)
    remaining = [s for s in ordered if s.status not in TERMINAL_STOP_STATUSES]

    progress = RouteProgress(
        total_stops=len(ordered),
        completed_stops=len([s for s in ordered if s.status == StopStatus.COMPLETED]),
        remaining_stops=len(remaining),
    )

    if not remaining:
        return progress

    progress.next_stop_sequence = remaining[0].sequence_number

    target_idx = None
    for idx, stop in enumerate(remaining):
        if stop.order_id == order_id and stop.stop_type == StopType.DELIVERY:
            target_idx = idx
            progress.target_stop_sequence = stop.sequence_number
            break

    if target_idx is not None:
        progress.route_slice_to_target = remaining[: target_idx + 1]

    return progress


async def _load_historical_eta_delay_seconds(
    db: AsyncSession,
    *,
    order_type,
    planned_hour_utc: int,
) -> int | None:
    result = await db.execute(
        select(
            func.count(RouteStop.id),
            func.avg(
                func.extract("epoch", RouteStop.actual_arrival - RouteStop.planned_arrival)
            ),
        )
        .join(Order, Order.id == RouteStop.order_id)
        .where(
            RouteStop.stop_type == StopType.DELIVERY,
            RouteStop.status == StopStatus.COMPLETED,
            RouteStop.actual_arrival.is_not(None),
            RouteStop.planned_arrival.is_not(None),
            Order.type == order_type,
            func.abs(func.extract("hour", RouteStop.planned_arrival) - planned_hour_utc) <= 1,
        )
    )

    row = result.one_or_none()
    if not row:
        return None

    sample_count = int(row[0] or 0)
    if sample_count < settings.ETA_CALIBRATION_MIN_SAMPLES:
        return None

    avg_delay_seconds = float(row[1] or 0.0)
    clamped = max(
        -settings.ETA_MAX_HISTORICAL_DELAY_SECONDS,
        min(settings.ETA_MAX_HISTORICAL_DELAY_SECONDS, round(avg_delay_seconds)),
    )
    return int(clamped)


async def compute_tracking_eta(
    db: AsyncSession,
    *,
    order: Order,
    courier_lat: float | None,
    courier_lon: float | None,
    now: datetime | None = None,
) -> EtaComputation:
    """Compute ETA and route progress for customer tracking."""
    started = time.monotonic()
    if (
        courier_lat is None
        or courier_lon is None
        or order.status in TERMINAL_ORDER_STATUSES
    ):
        return EtaComputation()

    now_ts = now or utcnow_naive()
    out = EtaComputation()

    eta_osrm_profile = "driving"
    if order.route_id:
        route_result = await db.execute(
            select(Route)
            .where(Route.id == order.route_id)
            .options(selectinload(Route.stops), selectinload(Route.courier))
        )
        route_obj = route_result.scalar_one_or_none()
        if route_obj and route_obj.courier:
            eta_osrm_profile = resolve_osrm_profile_from_values(
                route_obj.courier.vehicle_type,
                gross_weight_kg=round(route_obj.courier.max_weight_kg),
            )
        if route_obj and route_obj.stops:
            progress = build_route_progress(route_obj.stops, order_id=order.id)
            out.total_stops = progress.total_stops
            out.completed_stops = progress.completed_stops
            out.remaining_stops = progress.remaining_stops
            out.next_stop_sequence = progress.next_stop_sequence
            out.target_stop_sequence = progress.target_stop_sequence

            if progress.route_slice_to_target:
                coords = [(courier_lat, courier_lon)] + [
                    (stop.lat, stop.lon) for stop in progress.route_slice_to_target
                ]
                try:
                    matrix = await osrm_client.get_distance_matrix(
                        coords, profile=eta_osrm_profile
                    )
                    base_duration_s = sum_path_duration_seconds(matrix.durations)
                    duration_s = base_duration_s
                    adjusted_applied = False
                    try:
                        adjusted = await traffic_profile_service.adjust_duration_matrix(
                            db,
                            coords=coords,
                            durations=matrix.durations,
                            at_time=now_ts,
                        )
                        duration_s = sum_path_duration_seconds(adjusted.durations)
                        adjusted_applied = adjusted.applied
                    except Exception as exc:
                        logger.warning(
                            "ETA traffic profile adjustment failed on route sequence; using baseline durations: %s",
                            exc,
                        )
                    if adjusted_applied and out.eta_source is None:
                        out.eta_source = "route_sequence+traffic_profile"
                    elif out.eta_source is None:
                        out.eta_source = "route_sequence"
                    perf_metrics.observe_ms(
                        "eta_error_delta_ms",
                        abs(duration_s - base_duration_s) * 1000.0,
                    )
                    out.estimated_arrival = now_ts + timedelta(seconds=duration_s)
                    if out.eta_source is None:
                        out.eta_source = "route_sequence"
                except Exception as exc:
                    logger.warning(
                        "ETA route-sequence computation failed; falling back to direct route: %s",
                        exc,
                    )

    if out.estimated_arrival is None:
        try:
            route = await osrm_client.get_route(
                (courier_lat, courier_lon),
                (order.delivery_lat, order.delivery_lon),
                profile=eta_osrm_profile,
            )
            adjusted_duration_s = route.duration_s
            applied = False
            try:
                adjusted_duration_s, _multiplier, applied = await traffic_profile_service.adjust_single_duration(
                    db,
                    origin=(courier_lat, courier_lon),
                    destination=(order.delivery_lat, order.delivery_lon),
                    base_duration_s=route.duration_s,
                    at_time=now_ts,
                )
            except Exception as exc:
                logger.warning(
                    "ETA traffic profile adjustment failed on direct route; using baseline duration: %s",
                    exc,
                )
            out.estimated_arrival = now_ts + timedelta(seconds=adjusted_duration_s)
            out.eta_source = "direct_route+traffic_profile" if applied else "direct_route"
            perf_metrics.observe_ms(
                "eta_error_delta_ms",
                abs(adjusted_duration_s - route.duration_s) * 1000.0,
            )
        except Exception as exc:
            logger.warning("ETA direct-route computation failed: %s", exc)
            return out

    order_type = getattr(order, "type", None)
    if out.estimated_arrival is not None and order_type is not None:
        try:
            delay_adjustment = await _load_historical_eta_delay_seconds(
                db,
                order_type=order_type,
                planned_hour_utc=out.estimated_arrival.hour,
            )
        except Exception as exc:
            logger.warning("ETA historical-delay calibration lookup failed: %s", exc)
            delay_adjustment = None

        if delay_adjustment is not None and delay_adjustment != 0:
            out.estimated_arrival = out.estimated_arrival + timedelta(seconds=delay_adjustment)
            if out.eta_source:
                out.eta_source = f"{out.eta_source}+historical_delay"

    perf_metrics.observe_ms("eta_compute_latency_ms", (time.monotonic() - started) * 1000)

    return out
