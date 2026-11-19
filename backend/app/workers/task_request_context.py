from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Iterator

from app.utils.request_context import (
    bind_request_context,
    correlation_id_from_headers,
    parent_task_id_from_headers,
    request_id_from_headers,
)


@contextmanager
def bind_task_request_id(task_request: Any) -> Iterator[str]:
    headers = getattr(task_request, "headers", None)
    header_mapping = headers if isinstance(headers, Mapping) else None
    request_id = request_id_from_headers(header_mapping)
    correlation_id = correlation_id_from_headers(
        header_mapping,
        fallback=request_id,
    )
    task_id = getattr(task_request, "id", None)
    parent_task_id = parent_task_id_from_headers(header_mapping)
    with bind_request_context(
        request_id=request_id,
        correlation_id=correlation_id,
        task_id=task_id,
        parent_task_id=parent_task_id,
    ) as request_id:
        yield request_id
