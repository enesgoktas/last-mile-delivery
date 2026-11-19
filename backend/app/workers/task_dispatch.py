from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.utils.request_context import (
    CORRELATION_ID_HEADER,
    PARENT_TASK_ID_HEADER,
    REQUEST_ID_HEADER,
    current_correlation_id_or_new,
    current_request_id_or_new,
    current_task_id_or_none,
)


def current_task_headers() -> dict[str, str]:
    headers = {
        REQUEST_ID_HEADER: current_request_id_or_new(),
        CORRELATION_ID_HEADER: current_correlation_id_or_new(),
    }
    parent_task_id = current_task_id_or_none()
    if parent_task_id is not None:
        headers[PARENT_TASK_ID_HEADER] = parent_task_id
    return headers


def enqueue_task(
    task,
    *,
    args: Sequence[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    countdown: int | None = None,
):
    options: dict[str, Any] = {"headers": current_task_headers()}
    if args is not None:
        options["args"] = list(args)
    if kwargs is not None:
        options["kwargs"] = kwargs
    if countdown is not None:
        options["countdown"] = countdown
    return task.apply_async(**options)
