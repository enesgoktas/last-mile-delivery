from __future__ import annotations

import asyncio
import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

import httpx
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.courier import Courier
from app.models.enums import OrderStatus, StopStatus
from app.models.order import Order
from app.models.order_history import OrderStatusHistory
from app.models.route import Route, RouteStop
from app.models.tenant import Tenant
from app.models.user import User
from app.services import analytics_service
from app.services.analytics_range import AnalyticsReportRange, build_report_range
from app.utils.compat import UTC
from app.utils.tenant_context import get_current_tenant_id, tenant_filter

DEFAULT_PRIMARY_COLOR = "#0F766E"
DEFAULT_SECONDARY_COLOR = "#1D4ED8"
SUCCESS_COLOR = "#059669"
FAILURE_COLOR = "#DC2626"
TEXT_COLOR = "#0F172A"
MUTED_TEXT_COLOR = "#475569"
SURFACE_COLOR = "#F8FAFC"
BORDER_COLOR = "#CBD5E1"


@dataclass(frozen=True)
class DeliveryActivityEvent:
    order_id: uuid.UUID
    status: OrderStatus
    occurred_at: datetime
    latest_delivery: datetime
    courier_label: str


@dataclass(frozen=True)
class TrendBucket:
    label: str
    delivered_count: int
    failed_count: int
    on_time_rate: float | None


@dataclass(frozen=True)
class FailureReasonCount:
    label: str
    count: int


@dataclass(frozen=True)
class CourierPerformanceRow:
    courier_label: str
    delivered_count: int
    failed_count: int
    on_time_rate: float | None


@dataclass(frozen=True)
class MetricDelta:
    current: float
    previous: float
    absolute_change: float
    percent_change: float | None


@dataclass(frozen=True)
class OpenIssueSnapshot:
    pending_approval_runs: int
    critical_alerts: int
    failed_orders: int
    unassigned_orders: int
    total_open_items: int


@dataclass(frozen=True)
class DeliveryActivityReportPayload:
    tenant_name: str
    logo_bytes: bytes | None
    date_range_label: str
    generated_at_label: str
    timezone_name: str
    primary_color: str
    secondary_color: str
    total_days: int
    delivered_delta: MetricDelta
    failed_delta: MetricDelta
    on_time_delta: MetricDelta
    avg_daily_deliveries: float
    total_activity: int
    narrative: str
    trend_buckets: list[TrendBucket]
    failure_breakdown: list[FailureReasonCount]
    top_couriers: list[CourierPerformanceRow]
    insight_bullets: list[str]
    open_issues: OpenIssueSnapshot


def build_metric_delta(current: float, previous: float) -> MetricDelta:
    absolute_change = current - previous
    percent_change = None if previous == 0 else (absolute_change / previous) * 100
    return MetricDelta(
        current=current,
        previous=previous,
        absolute_change=absolute_change,
        percent_change=percent_change,
    )


def calculate_on_time_rate(events: Iterable[DeliveryActivityEvent]) -> float:
    delivered_events = [event for event in events if event.status == OrderStatus.DELIVERED]
    if not delivered_events:
        return 0.0
    on_time_deliveries = sum(1 for event in delivered_events if event.occurred_at <= event.latest_delivery)
    return on_time_deliveries / len(delivered_events)


def partition_events(
    events: Iterable[DeliveryActivityEvent],
    *,
    start: datetime,
    end: datetime,
) -> list[DeliveryActivityEvent]:
    return [event for event in events if start <= event.occurred_at <= end]


def build_trend_buckets(
    events: Iterable[DeliveryActivityEvent],
    report_range: AnalyticsReportRange,
) -> list[TrendBucket]:
    bucket_span_days = 1 if report_range.bucket_granularity == "day" else 7
    bucket_count = max(1, (report_range.total_days + bucket_span_days - 1) // bucket_span_days)

    buckets: list[dict[str, object]] = []
    current_start = report_range.local_start
    for _ in range(bucket_count):
        current_end = min(
            current_start + timedelta(days=bucket_span_days) - timedelta(microseconds=1),
            report_range.local_end,
        )
        buckets.append(
            {
                "start": current_start,
                "end": current_end,
                "delivered_count": 0,
                "failed_count": 0,
                "on_time_deliveries": 0,
            }
        )
        current_start = current_end + timedelta(microseconds=1)

    for event in events:
        localized = event.occurred_at.replace(tzinfo=UTC).astimezone(report_range.timezone)
        day_offset = (localized.date() - report_range.local_start.date()).days
        if day_offset < 0:
            continue
        bucket_index = min(day_offset // bucket_span_days, bucket_count - 1)
        bucket = buckets[bucket_index]
        if event.status == OrderStatus.DELIVERED:
            bucket["delivered_count"] = int(bucket["delivered_count"]) + 1
            if event.occurred_at <= event.latest_delivery:
                bucket["on_time_deliveries"] = int(bucket["on_time_deliveries"]) + 1
        elif event.status == OrderStatus.FAILED:
            bucket["failed_count"] = int(bucket["failed_count"]) + 1

    trend_buckets: list[TrendBucket] = []
    for bucket in buckets:
        bucket_start = bucket["start"]
        bucket_end = bucket["end"]
        assert isinstance(bucket_start, datetime)
        assert isinstance(bucket_end, datetime)
        delivered_count = int(bucket["delivered_count"])
        failed_count = int(bucket["failed_count"])
        on_time_deliveries = int(bucket["on_time_deliveries"])
        label = format_bucket_label(bucket_start, bucket_end, report_range.bucket_granularity)
        on_time_rate = (on_time_deliveries / delivered_count) if delivered_count else None
        trend_buckets.append(
            TrendBucket(
                label=label,
                delivered_count=delivered_count,
                failed_count=failed_count,
                on_time_rate=on_time_rate,
            )
        )
    return trend_buckets


def build_top_couriers(events: Iterable[DeliveryActivityEvent], *, limit: int = 10) -> list[CourierPerformanceRow]:
    by_courier: dict[str, dict[str, int]] = {}
    for event in events:
        label = event.courier_label or "Unattributed activity"
        stats = by_courier.setdefault(label, {"delivered": 0, "failed": 0, "on_time": 0})
        if event.status == OrderStatus.DELIVERED:
            stats["delivered"] += 1
            if event.occurred_at <= event.latest_delivery:
                stats["on_time"] += 1
        elif event.status == OrderStatus.FAILED:
            stats["failed"] += 1

    rows = [
        CourierPerformanceRow(
            courier_label=label,
            delivered_count=stats["delivered"],
            failed_count=stats["failed"],
            on_time_rate=(stats["on_time"] / stats["delivered"]) if stats["delivered"] else None,
        )
        for label, stats in by_courier.items()
        if stats["delivered"] > 0 or stats["failed"] > 0
    ]
    rows.sort(
        key=lambda row: (
            -(row.delivered_count + row.failed_count),
            -row.delivered_count,
            row.failed_count,
            row.courier_label.lower(),
        )
    )
    return rows[:limit]


def build_report_narrative(
    *,
    report_range: AnalyticsReportRange,
    delivered_delta: MetricDelta,
    failed_delta: MetricDelta,
    on_time_delta: MetricDelta,
    avg_daily_deliveries: float,
    total_activity: int,
) -> str:
    if total_activity == 0:
        return (
            f"No deliveries or failed attempts were recorded between {format_local_date(report_range.local_start)} "
            f"and {format_local_date(report_range.local_end)}. This PDF still includes the open-issue snapshot so "
            "operators can confirm whether the quiet period reflects expected demand or a data collection gap."
        )

    volume_clause = describe_delta(
        current=int(delivered_delta.current),
        previous=int(delivered_delta.previous),
        noun="completed deliveries",
    )
    failure_clause = describe_delta(
        current=int(failed_delta.current),
        previous=int(failed_delta.previous),
        noun="failed attempts",
    )
    on_time_pct = on_time_delta.current * 100
    on_time_vs_target = "above" if on_time_pct >= 95 else "below"

    return (
        f"From {format_local_date(report_range.local_start)} to {format_local_date(report_range.local_end)}, "
        f"the operation recorded {int(delivered_delta.current)} completed deliveries and {int(failed_delta.current)} "
        f"failed attempts. {volume_clause}. {failure_clause}. On-time performance closed at {on_time_pct:.1f}%, "
        f"{on_time_vs_target} the 95% target, while average daily delivery volume ran at {avg_daily_deliveries:.1f}."
    )


def build_insight_bullets(
    *,
    delivered_delta: MetricDelta,
    failed_delta: MetricDelta,
    top_couriers: list[CourierPerformanceRow],
    failure_breakdown: list[FailureReasonCount],
    open_issues: OpenIssueSnapshot,
    on_time_rate: float,
) -> list[str]:
    bullets: list[str] = []

    if top_couriers:
        lead_courier = top_couriers[0]
        bullets.append(
            f"{lead_courier.courier_label} handled the most tracked activity with "
            f"{lead_courier.delivered_count} deliveries and {lead_courier.failed_count} failed attempts."
        )

    if failure_breakdown:
        top_reason = failure_breakdown[0]
        bullets.append(
            f"{top_reason.label} was the most common failure reason, appearing {top_reason.count} time"
            f"{'' if top_reason.count == 1 else 's'} in the selected period."
        )
    elif failed_delta.current == 0:
        bullets.append("No failed delivery attempts were recorded in the selected window.")

    if open_issues.total_open_items > 0:
        bullets.append(
            f"The live queue currently contains {open_issues.total_open_items} open issues: "
            f"{open_issues.critical_alerts} critical alerts, {open_issues.failed_orders} failed orders, "
            f"and {open_issues.unassigned_orders} unassigned orders."
        )
    else:
        bullets.append(
            f"Current open-issue inventory is clear, and the period closed with an on-time rate of {on_time_rate * 100:.1f}%."
        )

    return bullets[:3]


def describe_delta(*, current: int, previous: int, noun: str) -> str:
    if previous == current:
        return f"{noun.capitalize()} held steady versus the previous comparison window."
    if previous == 0:
        return f"{noun.capitalize()} increased from 0 in the previous comparison window."

    change = current - previous
    direction = "increased" if change > 0 else "decreased"
    return (
        f"{noun.capitalize()} {direction} by {abs(change)} versus the previous comparison window "
        f"({previous} previously)."
    )


def format_bucket_label(bucket_start: datetime, bucket_end: datetime, granularity: str) -> str:
    if granularity == "day":
        return bucket_start.strftime("%b %d")
    if bucket_start.date() == bucket_end.date():
        return bucket_start.strftime("%b %d")
    return f"{bucket_start.strftime('%b %d')} - {bucket_end.strftime('%b %d')}"


def format_local_date(value: datetime) -> str:
    return value.strftime("%b %d, %Y")


def format_report_date_range(report_range: AnalyticsReportRange) -> str:
    return f"{format_local_date(report_range.local_start)} - {format_local_date(report_range.local_end)}"


def normalize_hex_color(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    if len(value) == 7 and value.startswith("#"):
        return value
    return fallback


async def generate_delivery_activity_report_pdf(
    db: AsyncSession,
    principal,
    *,
    date_from: datetime,
    date_to: datetime,
    timezone_name: str | None = None,
) -> bytes:
    report_range = build_report_range(date_from, date_to, timezone_name)
    tenant_id = get_current_tenant_id(principal)
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one()

    all_events = await _load_activity_events(db, principal, report_range)
    current_events = partition_events(all_events, start=report_range.utc_start, end=report_range.utc_end)
    previous_events = partition_events(
        all_events,
        start=report_range.previous_utc_start,
        end=report_range.previous_utc_end,
    )

    delivered_current = [event for event in current_events if event.status == OrderStatus.DELIVERED]
    delivered_previous = [event for event in previous_events if event.status == OrderStatus.DELIVERED]
    failed_current = [event for event in current_events if event.status == OrderStatus.FAILED]
    failed_previous = [event for event in previous_events if event.status == OrderStatus.FAILED]

    delivered_delta = build_metric_delta(len(delivered_current), len(delivered_previous))
    failed_delta = build_metric_delta(len(failed_current), len(failed_previous))
    on_time_delta = build_metric_delta(
        calculate_on_time_rate(current_events),
        calculate_on_time_rate(previous_events),
    )
    avg_daily_deliveries = len(delivered_current) / report_range.total_days if report_range.total_days else 0.0
    total_activity = len(current_events)
    trend_buckets = build_trend_buckets(current_events, report_range)
    failure_breakdown = await _load_failure_breakdown(db, principal, report_range)
    top_couriers = build_top_couriers(current_events)
    open_issues = await _load_open_issue_snapshot(db, principal)
    logo_bytes = await _fetch_logo_bytes(tenant.logo_url)
    primary_color = normalize_hex_color(tenant.primary_color, DEFAULT_PRIMARY_COLOR)
    secondary_color = normalize_hex_color(tenant.secondary_color, DEFAULT_SECONDARY_COLOR)
    narrative = build_report_narrative(
        report_range=report_range,
        delivered_delta=delivered_delta,
        failed_delta=failed_delta,
        on_time_delta=on_time_delta,
        avg_daily_deliveries=avg_daily_deliveries,
        total_activity=total_activity,
    )
    insight_bullets = build_insight_bullets(
        delivered_delta=delivered_delta,
        failed_delta=failed_delta,
        top_couriers=top_couriers,
        failure_breakdown=failure_breakdown,
        open_issues=open_issues,
        on_time_rate=on_time_delta.current,
    )

    payload = DeliveryActivityReportPayload(
        tenant_name=tenant.name,
        logo_bytes=logo_bytes,
        date_range_label=format_report_date_range(report_range),
        generated_at_label=datetime.now(UTC).astimezone(report_range.timezone).strftime("%b %d, %Y %H:%M"),
        timezone_name=report_range.timezone_name,
        primary_color=primary_color,
        secondary_color=secondary_color,
        total_days=report_range.total_days,
        delivered_delta=delivered_delta,
        failed_delta=failed_delta,
        on_time_delta=on_time_delta,
        avg_daily_deliveries=avg_daily_deliveries,
        total_activity=total_activity,
        narrative=narrative,
        trend_buckets=trend_buckets,
        failure_breakdown=failure_breakdown,
        top_couriers=top_couriers,
        insight_bullets=insight_bullets,
        open_issues=open_issues,
    )
    return await asyncio.to_thread(render_delivery_activity_report_pdf, payload)


async def _load_activity_events(
    db: AsyncSession,
    principal,
    report_range: AnalyticsReportRange,
) -> list[DeliveryActivityEvent]:
    actor_courier = aliased(Courier)
    actor_user = aliased(User)
    tenant_id = get_current_tenant_id(principal)

    result = await db.execute(
        select(
            OrderStatusHistory.order_id,
            OrderStatusHistory.to_status,
            OrderStatusHistory.created_at,
            Order.latest_delivery,
            actor_user.email,
        )
        .join(Order, Order.id == OrderStatusHistory.order_id)
        .outerjoin(
            actor_courier,
            and_(
                actor_courier.user_id == OrderStatusHistory.actor_user_id,
                actor_courier.tenant_id == tenant_id,
            ),
        )
        .outerjoin(actor_user, actor_user.id == actor_courier.user_id)
        .where(
            tenant_filter(Order, principal),
            OrderStatusHistory.to_status.in_([OrderStatus.DELIVERED, OrderStatus.FAILED]),
            OrderStatusHistory.created_at >= report_range.previous_utc_start,
            OrderStatusHistory.created_at <= report_range.utc_end,
        )
        .order_by(OrderStatusHistory.created_at.asc())
    )

    events: list[DeliveryActivityEvent] = []
    for order_id, to_status, occurred_at, latest_delivery, courier_email in result.all():
        if latest_delivery is None:
            continue
        events.append(
            DeliveryActivityEvent(
                order_id=order_id,
                status=to_status,
                occurred_at=occurred_at,
                latest_delivery=latest_delivery,
                courier_label=courier_email or "Unattributed activity",
            )
        )
    return events


async def _load_failure_breakdown(
    db: AsyncSession,
    principal,
    report_range: AnalyticsReportRange,
) -> list[FailureReasonCount]:
    latest_failed_route_per_order = (
        select(
            RouteStop.order_id.label("order_id"),
            func.max(Route.created_at).label("latest_route_created_at"),
        )
        .join(Route, Route.id == RouteStop.route_id)
        .join(Order, Order.id == RouteStop.order_id)
        .where(
            tenant_filter(RouteStop, principal),
            RouteStop.order_id.is_not(None),
            RouteStop.status == StopStatus.FAILED,
            Order.failed_at.is_not(None),
            Order.failed_at >= report_range.utc_start,
            Order.failed_at <= report_range.utc_end,
        )
        .group_by(RouteStop.order_id)
        .subquery()
    )

    result = await db.execute(
        select(
            RouteStop.failure_reason,
            RouteStop.failure_reason_detail,
            func.count(RouteStop.id),
        )
        .join(Route, Route.id == RouteStop.route_id)
        .join(
            latest_failed_route_per_order,
            and_(
                RouteStop.order_id == latest_failed_route_per_order.c.order_id,
                Route.created_at == latest_failed_route_per_order.c.latest_route_created_at,
            ),
        )
        .where(
            tenant_filter(RouteStop, principal),
            RouteStop.status == StopStatus.FAILED,
        )
        .group_by(RouteStop.failure_reason, RouteStop.failure_reason_detail)
        .order_by(func.count(RouteStop.id).desc())
    )

    rows: list[FailureReasonCount] = []
    for failure_reason, detail, count in result.all():
        label = detail or (failure_reason.value.replace("_", " ").title() if failure_reason is not None else "Unknown")
        rows.append(FailureReasonCount(label=label, count=int(count)))
    return rows


async def _load_open_issue_snapshot(db: AsyncSession, principal) -> OpenIssueSnapshot:
    exception_center = await analytics_service.get_exception_center_summary(db, principal, limit=0)
    counts = exception_center["counts"]
    return OpenIssueSnapshot(
        pending_approval_runs=counts["pending_approval_runs"],
        critical_alerts=counts["critical_alerts"],
        failed_orders=counts["failed_orders"],
        unassigned_orders=counts["unassigned_orders"],
        total_open_items=counts["total_open_items"],
    )


async def _fetch_logo_bytes(logo_url: str | None) -> bytes | None:
    if not logo_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
            response = await client.get(logo_url)
            response.raise_for_status()
    except Exception:
        return None
    return response.content


def render_delivery_activity_report_pdf(payload: DeliveryActivityReportPayload) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph, Table, TableStyle

    pdf_buffer = io.BytesIO()
    pdf = canvas.Canvas(pdf_buffer, pagesize=A4)
    width, height = A4
    margin = 36

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor(TEXT_COLOR),
    )
    small_style = ParagraphStyle(
        "ReportSmall",
        parent=body_style,
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor(MUTED_TEXT_COLOR),
    )
    bullet_style = ParagraphStyle(
        "ReportBullet",
        parent=body_style,
        bulletIndent=10,
        leftIndent=18,
        spaceAfter=4,
    )

    _draw_cover_page(
        pdf,
        payload,
        width=width,
        height=height,
        margin=margin,
        body_style=body_style,
        small_style=small_style,
        paragraph_cls=Paragraph,
        image_reader_cls=ImageReader,
        table_cls=Table,
        table_style_cls=TableStyle,
        colors_mod=colors,
    )
    pdf.showPage()
    _draw_details_page(
        pdf,
        payload,
        width=width,
        height=height,
        margin=margin,
        body_style=body_style,
        small_style=small_style,
        bullet_style=bullet_style,
        paragraph_cls=Paragraph,
        image_reader_cls=ImageReader,
        table_cls=Table,
        table_style_cls=TableStyle,
        colors_mod=colors,
    )
    pdf.save()
    return pdf_buffer.getvalue()


def _draw_cover_page(
    pdf,
    payload: DeliveryActivityReportPayload,
    *,
    width: float,
    height: float,
    margin: float,
    body_style,
    small_style,
    paragraph_cls,
    image_reader_cls,
    table_cls,
    table_style_cls,
    colors_mod,
) -> None:
    primary = _hex_to_color(colors_mod, payload.primary_color)
    secondary = _hex_to_color(colors_mod, payload.secondary_color)
    pdf.setFillColor(primary)
    pdf.rect(0, height - 120, width, 120, fill=1, stroke=0)

    pdf.setFillColor(colors_mod.white)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(margin, height - 58, "Delivery Activity Report")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(margin, height - 78, payload.tenant_name)
    pdf.drawString(margin, height - 94, payload.date_range_label)
    pdf.drawRightString(width - margin, height - 58, f"Generated {payload.generated_at_label}")
    pdf.drawRightString(width - margin, height - 74, payload.timezone_name)

    if payload.logo_bytes:
        try:
            logo_reader = image_reader_cls(io.BytesIO(payload.logo_bytes))
            pdf.drawImage(logo_reader, width - margin - 62, height - 110, width=50, height=50, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    kpi_y = height - 230
    kpi_width = (width - (margin * 2) - 18) / 4
    kpis = [
        ("Completed", int(payload.delivered_delta.current), _format_count_delta(payload.delivered_delta), SUCCESS_COLOR),
        ("Failed", int(payload.failed_delta.current), _format_count_delta(payload.failed_delta), FAILURE_COLOR),
        ("On-Time Rate", f"{payload.on_time_delta.current * 100:.1f}%", _format_rate_delta(payload.on_time_delta), payload.secondary_color),
        ("Avg / Day", f"{payload.avg_daily_deliveries:.1f}", f"{payload.total_activity} total tracked activities", payload.primary_color),
    ]
    for index, (label, value, subtext, color_hex) in enumerate(kpis):
        tile_x = margin + index * (kpi_width + 6)
        _draw_stat_tile(
            pdf,
            x=tile_x,
            y=kpi_y,
            width=kpi_width,
            height=74,
            label=label,
            value=str(value),
            subtext=subtext,
            accent_hex=color_hex,
            colors_mod=colors_mod,
        )

    summary_top = kpi_y - 18
    _draw_card(pdf, margin, summary_top - 100, width - (margin * 2), 100, colors_mod)
    _draw_paragraph(
        pdf,
        payload.narrative,
        x=margin + 14,
        y=summary_top - 16,
        width=width - (margin * 2) - 28,
        style=body_style,
        paragraph_cls=paragraph_cls,
    )

    trend_image = _build_volume_trend_chart(payload)
    on_time_image = _build_on_time_chart(payload)
    if trend_image is not None:
        pdf.drawImage(
            image_reader_cls(io.BytesIO(trend_image)),
            margin,
            225,
            width=width - (margin * 2),
            height=155,
            preserveAspectRatio=False,
            mask="auto",
        )
    if on_time_image is not None:
        pdf.drawImage(
            image_reader_cls(io.BytesIO(on_time_image)),
            margin,
            50,
            width=width - (margin * 2),
            height=145,
            preserveAspectRatio=False,
            mask="auto",
        )


def _draw_details_page(
    pdf,
    payload: DeliveryActivityReportPayload,
    *,
    width: float,
    height: float,
    margin: float,
    body_style,
    small_style,
    bullet_style,
    paragraph_cls,
    image_reader_cls,
    table_cls,
    table_style_cls,
    colors_mod,
) -> None:
    pdf.setFillColor(_hex_to_color(colors_mod, payload.primary_color))
    pdf.rect(0, height - 54, width, 54, fill=1, stroke=0)
    pdf.setFillColor(colors_mod.white)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(margin, height - 34, "Operational Detail")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - margin, height - 33, payload.date_range_label)

    chart_image = _build_failure_reason_chart(payload)
    if chart_image is not None:
        pdf.drawImage(
            image_reader_cls(io.BytesIO(chart_image)),
            margin,
            505,
            width=width - (margin * 2),
            height=210,
            preserveAspectRatio=False,
            mask="auto",
        )

    left_col_width = (width - (margin * 2) - 12) * 0.58
    right_col_x = margin + left_col_width + 12
    right_col_width = width - right_col_x - margin

    _draw_card(pdf, margin, 332, left_col_width, 148, colors_mod)
    pdf.setFillColor(_hex_to_color(colors_mod, payload.primary_color))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(margin + 14, 462, "Top Courier Activity")
    courier_table = _build_courier_table(
        payload,
        width=left_col_width - 20,
        table_cls=table_cls,
        table_style_cls=table_style_cls,
        colors_mod=colors_mod,
    )
    courier_table.wrapOn(pdf, left_col_width - 20, 118)
    courier_table.drawOn(pdf, margin + 10, 346)

    _draw_card(pdf, right_col_x, 396, right_col_width, 84, colors_mod)
    pdf.setFillColor(_hex_to_color(colors_mod, payload.primary_color))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(right_col_x + 14, 462, "Current Open Issues")
    _draw_open_issue_summary(
        pdf,
        payload.open_issues,
        x=right_col_x + 14,
        y=444,
        width=right_col_width - 28,
        colors_mod=colors_mod,
    )

    _draw_card(pdf, right_col_x, 236, right_col_width, 148, colors_mod)
    pdf.setFillColor(_hex_to_color(colors_mod, payload.primary_color))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(right_col_x + 14, 366, "Notable Insights")
    bullet_y = 350
    for bullet in payload.insight_bullets:
        bullet_y = _draw_paragraph(
            pdf,
            bullet,
            x=right_col_x + 14,
            y=bullet_y,
            width=right_col_width - 28,
            style=bullet_style,
            paragraph_cls=paragraph_cls,
            bullet_text="•",
        ) - 2

    _draw_card(pdf, margin, 120, width - (margin * 2), 88, colors_mod)
    pdf.setFillColor(_hex_to_color(colors_mod, payload.primary_color))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin + 14, 186, "Interpretation Notes")
    _draw_paragraph(
        pdf,
        "This report is based on delivery status-history events recorded during the selected local-time window. "
        "Volume trends and on-time performance use those event timestamps, while the open-issue section is a live snapshot taken at download time.",
        x=margin + 14,
        y=170,
        width=width - (margin * 2) - 28,
        style=small_style,
        paragraph_cls=paragraph_cls,
    )


def _draw_card(pdf, x: float, y: float, width: float, height: float, colors_mod) -> None:
    pdf.setFillColor(_hex_to_color(colors_mod, SURFACE_COLOR))
    pdf.setStrokeColor(_hex_to_color(colors_mod, BORDER_COLOR))
    pdf.roundRect(x, y, width, height, 12, fill=1, stroke=1)


def _draw_stat_tile(
    pdf,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    label: str,
    value: str,
    subtext: str,
    accent_hex: str,
    colors_mod,
) -> None:
    _draw_card(pdf, x, y, width, height, colors_mod)
    pdf.setFillColor(_hex_to_color(colors_mod, accent_hex))
    pdf.rect(x, y + height - 6, width, 6, fill=1, stroke=0)
    pdf.setFillColor(_hex_to_color(colors_mod, MUTED_TEXT_COLOR))
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(x + 10, y + height - 18, label.upper())
    pdf.setFillColor(_hex_to_color(colors_mod, TEXT_COLOR))
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(x + 10, y + 30, value)
    pdf.setFillColor(_hex_to_color(colors_mod, MUTED_TEXT_COLOR))
    pdf.setFont("Helvetica", 8.5)
    pdf.drawString(x + 10, y + 14, subtext)


def _draw_paragraph(
    pdf,
    text: str,
    *,
    x: float,
    y: float,
    width: float,
    style,
    paragraph_cls,
    bullet_text: str | None = None,
) -> float:
    paragraph = paragraph_cls(text, style, bulletText=bullet_text)
    wrapped_width, wrapped_height = paragraph.wrap(width, 2000)
    paragraph.drawOn(pdf, x, y - wrapped_height)
    return y - wrapped_height


def _build_courier_table(payload, *, width: float, table_cls, table_style_cls, colors_mod):
    rows = [["Courier", "Delivered", "Failed", "On-Time"]]
    for row in payload.top_couriers[:10]:
        rows.append(
            [
                row.courier_label,
                str(row.delivered_count),
                str(row.failed_count),
                "—" if row.on_time_rate is None else f"{row.on_time_rate * 100:.1f}%",
            ]
        )
    if len(rows) == 1:
        rows.append(["No courier activity recorded", "—", "—", "—"])

    table = table_cls(rows, colWidths=[width * 0.50, width * 0.16, width * 0.14, width * 0.20])
    table.setStyle(
        table_style_cls(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _hex_to_color(colors_mod, payload.primary_color)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors_mod.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("TEXTCOLOR", (0, 1), (-1, -1), _hex_to_color(colors_mod, TEXT_COLOR)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_hex_to_color(colors_mod, "#FFFFFF"), _hex_to_color(colors_mod, "#F8FAFC")]),
                ("GRID", (0, 0), (-1, -1), 0.5, _hex_to_color(colors_mod, BORDER_COLOR)),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _draw_open_issue_summary(pdf, snapshot: OpenIssueSnapshot, *, x: float, y: float, width: float, colors_mod) -> None:
    line_height = 14
    summary_rows = [
        ("Total open items", snapshot.total_open_items, payload_color(snapshot.total_open_items)),
        ("Critical alerts", snapshot.critical_alerts, FAILURE_COLOR),
        ("Failed orders", snapshot.failed_orders, DEFAULT_SECONDARY_COLOR),
        ("Unassigned orders", snapshot.unassigned_orders, DEFAULT_PRIMARY_COLOR),
        ("Pending approvals", snapshot.pending_approval_runs, MUTED_TEXT_COLOR),
    ]
    current_y = y
    for label, value, color_hex in summary_rows:
        pdf.setFillColor(_hex_to_color(colors_mod, MUTED_TEXT_COLOR))
        pdf.setFont("Helvetica", 9)
        pdf.drawString(x, current_y, label)
        pdf.setFillColor(_hex_to_color(colors_mod, color_hex))
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawRightString(x + width, current_y, str(value))
        current_y -= line_height


def payload_color(total_open_items: int) -> str:
    return FAILURE_COLOR if total_open_items > 0 else SUCCESS_COLOR


def _format_count_delta(delta: MetricDelta) -> str:
    if delta.previous == delta.current:
        return "Flat vs previous period"
    direction = "up" if delta.absolute_change > 0 else "down"
    return f"{direction} {abs(int(delta.absolute_change))} vs previous period"


def _format_rate_delta(delta: MetricDelta) -> str:
    basis_points = abs(delta.absolute_change) * 100
    if delta.previous == delta.current:
        return "Flat vs previous period"
    direction = "up" if delta.absolute_change > 0 else "down"
    return f"{direction} {basis_points:.1f} pts vs previous period"


def _build_volume_trend_chart(payload: DeliveryActivityReportPayload) -> bytes | None:
    from matplotlib.figure import Figure

    figure = Figure(figsize=(8.27, 2.35), dpi=160)
    axis = figure.subplots()
    labels = [bucket.label for bucket in payload.trend_buckets]
    delivered = [bucket.delivered_count for bucket in payload.trend_buckets]
    failed = [bucket.failed_count for bucket in payload.trend_buckets]

    axis.plot(labels, delivered, color=SUCCESS_COLOR, linewidth=2.4, marker="o", label="Delivered")
    axis.plot(labels, failed, color=FAILURE_COLOR, linewidth=2.2, marker="o", label="Failed")
    axis.set_title("Delivered vs Failed Activity")
    axis.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    axis.legend(loc="upper left", frameon=False)
    axis.set_ylabel("Events")
    if not any(delivered) and not any(failed):
        axis.text(0.5, 0.5, "No delivery activity in this period", ha="center", va="center", transform=axis.transAxes)
    figure.tight_layout()
    return _figure_to_png(figure)


def _build_on_time_chart(payload: DeliveryActivityReportPayload) -> bytes | None:
    from matplotlib.figure import Figure

    figure = Figure(figsize=(8.27, 2.2), dpi=160)
    axis = figure.subplots()
    labels = [bucket.label for bucket in payload.trend_buckets]
    values = [(bucket.on_time_rate * 100) if bucket.on_time_rate is not None else None for bucket in payload.trend_buckets]
    plotted_values = [value if value is not None else 0 for value in values]

    axis.plot(labels, plotted_values, color=payload.secondary_color, linewidth=2.4, marker="o")
    axis.axhline(95, color=BORDER_COLOR, linestyle="--", linewidth=1.0, label="95% target")
    axis.set_ylim(0, 105)
    axis.set_ylabel("Rate %")
    axis.set_title("On-Time Delivery Rate")
    axis.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    if all(value is None for value in values):
        axis.text(0.5, 0.5, "No completed deliveries in this period", ha="center", va="center", transform=axis.transAxes)
    figure.tight_layout()
    return _figure_to_png(figure)


def _build_failure_reason_chart(payload: DeliveryActivityReportPayload) -> bytes | None:
    from matplotlib.figure import Figure

    figure = Figure(figsize=(8.27, 2.8), dpi=160)
    axis = figure.subplots()

    if not payload.failure_breakdown:
        axis.text(0.5, 0.5, "No failed delivery attempts in this period", ha="center", va="center", transform=axis.transAxes)
        axis.set_axis_off()
        figure.tight_layout()
        return _figure_to_png(figure)

    reasons = payload.failure_breakdown[:6]
    labels = [reason.label for reason in reasons]
    counts = [reason.count for reason in reasons]
    axis.barh(labels, counts, color=payload.primary_color)
    axis.set_title("Failure Reasons")
    axis.invert_yaxis()
    axis.grid(axis="x", linestyle="--", linewidth=0.6, alpha=0.35)
    axis.set_xlabel("Count")
    figure.tight_layout()
    return _figure_to_png(figure)


def _figure_to_png(figure) -> bytes:
    output = io.BytesIO()
    figure.savefig(output, format="png", bbox_inches="tight")
    return output.getvalue()


def _hex_to_color(colors_mod, color_hex: str):
    return colors_mod.HexColor(color_hex)
