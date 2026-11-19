from datetime import datetime
import uuid

import pytest
from fastapi import HTTPException

from app.models.enums import OrderStatus
from app.services.analytics_range import build_report_range
from app.services.analytics_report_service import (
    CourierPerformanceRow,
    DeliveryActivityEvent,
    DeliveryActivityReportPayload,
    FailureReasonCount,
    OpenIssueSnapshot,
    TrendBucket,
    build_insight_bullets,
    build_metric_delta,
    build_report_narrative,
    build_top_couriers,
    build_trend_buckets,
    calculate_on_time_rate,
    render_delivery_activity_report_pdf,
)


def test_build_report_range_rejects_invalid_timezone():
    with pytest.raises(HTTPException) as excinfo:
        build_report_range(
            datetime(2026, 3, 1, 0, 0, 0),
            datetime(2026, 3, 30, 23, 59, 59),
            "Mars/Olympus",
        )

    assert excinfo.value.status_code == 422
    assert "Invalid timezone" in str(excinfo.value.detail)


def test_build_report_range_caps_synchronous_reports_at_92_days():
    with pytest.raises(HTTPException) as excinfo:
        build_report_range(
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 4, 15, 23, 59, 59),
            "UTC",
        )

    assert excinfo.value.status_code == 422
    assert "92 days" in str(excinfo.value.detail)


def test_calculate_on_time_rate_uses_delivery_event_timestamps():
    events = [
      DeliveryActivityEvent(
          order_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
          status=OrderStatus.DELIVERED,
          occurred_at=datetime(2026, 3, 10, 9, 30, 0),
          latest_delivery=datetime(2026, 3, 10, 10, 0, 0),
          courier_label="courier-a@example.com",
      ),
      DeliveryActivityEvent(
          order_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
          status=OrderStatus.DELIVERED,
          occurred_at=datetime(2026, 3, 10, 12, 0, 0),
          latest_delivery=datetime(2026, 3, 10, 11, 0, 0),
          courier_label="courier-b@example.com",
      ),
    ]

    assert calculate_on_time_rate(events) == 0.5


def test_build_trend_buckets_groups_daily_activity():
    report_range = build_report_range(
        datetime(2026, 3, 1, 0, 0, 0),
        datetime(2026, 3, 3, 23, 59, 59),
        "UTC",
    )
    events = [
        DeliveryActivityEvent(
            order_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            status=OrderStatus.DELIVERED,
            occurred_at=datetime(2026, 3, 1, 8, 0, 0),
            latest_delivery=datetime(2026, 3, 1, 9, 0, 0),
            courier_label="courier-a@example.com",
        ),
        DeliveryActivityEvent(
            order_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            status=OrderStatus.FAILED,
            occurred_at=datetime(2026, 3, 2, 8, 0, 0),
            latest_delivery=datetime(2026, 3, 2, 9, 0, 0),
            courier_label="courier-b@example.com",
        ),
        DeliveryActivityEvent(
            order_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
            status=OrderStatus.DELIVERED,
            occurred_at=datetime(2026, 3, 3, 11, 0, 0),
            latest_delivery=datetime(2026, 3, 3, 10, 30, 0),
            courier_label="courier-a@example.com",
        ),
    ]

    buckets = build_trend_buckets(events, report_range)

    assert [bucket.label for bucket in buckets] == ["Mar 01", "Mar 02", "Mar 03"]
    assert buckets[0].delivered_count == 1
    assert buckets[1].failed_count == 1
    assert buckets[2].on_time_rate == 0.0


def test_build_top_couriers_summarizes_delivery_and_failure_counts():
    events = [
        DeliveryActivityEvent(
            order_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            status=OrderStatus.DELIVERED,
            occurred_at=datetime(2026, 3, 1, 8, 0, 0),
            latest_delivery=datetime(2026, 3, 1, 9, 0, 0),
            courier_label="courier-a@example.com",
        ),
        DeliveryActivityEvent(
            order_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            status=OrderStatus.DELIVERED,
            occurred_at=datetime(2026, 3, 1, 10, 0, 0),
            latest_delivery=datetime(2026, 3, 1, 9, 30, 0),
            courier_label="courier-a@example.com",
        ),
        DeliveryActivityEvent(
            order_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
            status=OrderStatus.FAILED,
            occurred_at=datetime(2026, 3, 1, 12, 0, 0),
            latest_delivery=datetime(2026, 3, 1, 12, 30, 0),
            courier_label="courier-b@example.com",
        ),
    ]

    rows = build_top_couriers(events)

    assert rows[0] == CourierPerformanceRow(
        courier_label="courier-a@example.com",
        delivered_count=2,
        failed_count=0,
        on_time_rate=0.5,
    )
    assert rows[1].courier_label == "courier-b@example.com"
    assert rows[1].failed_count == 1


def test_build_report_narrative_handles_empty_periods():
    report_range = build_report_range(
        datetime(2026, 3, 1, 0, 0, 0),
        datetime(2026, 3, 7, 23, 59, 59),
        "UTC",
    )

    narrative = build_report_narrative(
        report_range=report_range,
        delivered_delta=build_metric_delta(0, 4),
        failed_delta=build_metric_delta(0, 1),
        on_time_delta=build_metric_delta(0, 0.75),
        avg_daily_deliveries=0.0,
        total_activity=0,
    )

    assert "No deliveries or failed attempts were recorded" in narrative


def test_build_insight_bullets_prefers_failure_breakdown_detail_labels():
    bullets = build_insight_bullets(
        delivered_delta=build_metric_delta(12, 9),
        failed_delta=build_metric_delta(3, 4),
        top_couriers=[
            CourierPerformanceRow(
                courier_label="courier-a@example.com",
                delivered_count=7,
                failed_count=1,
                on_time_rate=0.86,
            )
        ],
        failure_breakdown=[FailureReasonCount(label="Gate code missing", count=2)],
        open_issues=OpenIssueSnapshot(
            pending_approval_runs=0,
            critical_alerts=1,
            failed_orders=2,
            unassigned_orders=1,
            total_open_items=4,
        ),
        on_time_rate=0.92,
    )

    assert any("Gate code missing" in bullet for bullet in bullets)
    assert any("courier-a@example.com" in bullet for bullet in bullets)


def test_render_delivery_activity_report_pdf_returns_pdf_bytes():
    pdf_bytes = render_delivery_activity_report_pdf(
        DeliveryActivityReportPayload(
            tenant_name="Last Mile Ops",
            logo_bytes=None,
            date_range_label="Mar 01, 2026 - Mar 30, 2026",
            generated_at_label="Mar 30, 2026 12:30",
            timezone_name="UTC",
            primary_color="#0F766E",
            secondary_color="#1D4ED8",
            total_days=30,
            delivered_delta=build_metric_delta(95, 90),
            failed_delta=build_metric_delta(10, 14),
            on_time_delta=build_metric_delta(0.91, 0.88),
            avg_daily_deliveries=3.2,
            total_activity=105,
            narrative="A steady delivery period with improving failure counts.",
            trend_buckets=[
                TrendBucket(label="Mar 01", delivered_count=10, failed_count=1, on_time_rate=0.9),
                TrendBucket(label="Mar 02", delivered_count=11, failed_count=2, on_time_rate=0.82),
            ],
            failure_breakdown=[FailureReasonCount(label="Not Home", count=4)],
            top_couriers=[
                CourierPerformanceRow(
                    courier_label="courier-a@example.com",
                    delivered_count=12,
                    failed_count=1,
                    on_time_rate=0.92,
                )
            ],
            insight_bullets=[
                "courier-a@example.com handled the most tracked activity.",
                "Not Home was the most common failure reason.",
                "The live queue currently contains 3 open issues.",
            ],
            open_issues=OpenIssueSnapshot(
                pending_approval_runs=0,
                critical_alerts=1,
                failed_orders=1,
                unassigned_orders=1,
                total_open_items=3,
            ),
        )
    )

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1_000
