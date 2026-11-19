from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status

from app.utils.compat import UTC, to_naive_utc

MAX_ANALYTICS_REPORT_DAYS = 92
DAILY_BUCKET_THRESHOLD_DAYS = 31


@dataclass(frozen=True)
class AnalyticsReportRange:
    timezone_name: str
    timezone: ZoneInfo
    local_start: datetime
    local_end: datetime
    utc_start: datetime
    utc_end: datetime
    total_days: int
    bucket_granularity: str
    previous_local_start: datetime
    previous_local_end: datetime
    previous_utc_start: datetime
    previous_utc_end: datetime


def resolve_query_timezone(timezone_name: str | None) -> ZoneInfo:
    if timezone_name is None:
        return ZoneInfo(str(UTC))
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid timezone: {timezone_name}",
        ) from exc


def normalize_query_datetime(value: datetime | None, timezone_name: str | None = None) -> datetime | None:
    if value is None:
        return None

    timezone = resolve_query_timezone(timezone_name)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    else:
        value = value.astimezone(timezone)
    return to_naive_utc(value)


def build_report_range(
    date_from: datetime,
    date_to: datetime,
    timezone_name: str | None = None,
) -> AnalyticsReportRange:
    timezone = resolve_query_timezone(timezone_name)

    local_start = date_from.replace(tzinfo=timezone) if date_from.tzinfo is None else date_from.astimezone(timezone)
    local_end = date_to.replace(tzinfo=timezone) if date_to.tzinfo is None else date_to.astimezone(timezone)

    if local_start > local_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must be less than or equal to date_to",
        )

    total_days = (local_end.date() - local_start.date()).days + 1
    if total_days > MAX_ANALYTICS_REPORT_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Date range cannot exceed {MAX_ANALYTICS_REPORT_DAYS} days",
        )

    utc_start = to_naive_utc(local_start)
    utc_end = to_naive_utc(local_end)
    previous_local_start = local_start - timedelta(days=total_days)
    previous_local_end = local_end - timedelta(days=total_days)
    previous_utc_start = to_naive_utc(previous_local_start)
    previous_utc_end = to_naive_utc(previous_local_end)

    return AnalyticsReportRange(
        timezone_name=timezone.key,
        timezone=timezone,
        local_start=local_start,
        local_end=local_end,
        utc_start=utc_start,
        utc_end=utc_end,
        total_days=total_days,
        bucket_granularity="day" if total_days <= DAILY_BUCKET_THRESHOLD_DAYS else "week",
        previous_local_start=previous_local_start,
        previous_local_end=previous_local_end,
        previous_utc_start=previous_utc_start,
        previous_utc_end=previous_utc_end,
    )
