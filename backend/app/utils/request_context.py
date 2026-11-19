import contextvars
import logging
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Iterator

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"
PARENT_TASK_ID_HEADER = "X-Parent-Task-ID"
_REQUEST_ID_LOGGING_FLAG = "_request_id_logging_configured"
_REQUEST_CONTEXT_LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] "
    "[request_id=%(request_id)s correlation_id=%(correlation_id)s "
    "task_id=%(task_id)s parent_task_id=%(parent_task_id)s] %(message)s"
)
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="-",
)
_correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id",
    default="-",
)
_task_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_id",
    default="-",
)
_parent_task_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "parent_task_id",
    default="-",
)


def get_request_id() -> str:
    return _request_id_ctx.get()


def get_correlation_id() -> str:
    return _correlation_id_ctx.get()


def get_task_id() -> str:
    return _task_id_ctx.get()


def get_parent_task_id() -> str:
    return _parent_task_id_ctx.get()


def new_request_id() -> str:
    return str(uuid.uuid4())


def _normalize_context_value(value: object | None, *, fallback: str) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized and normalized != "-":
            return normalized
    return fallback


def normalize_request_id(value: object | None) -> str:
    return _normalize_context_value(value, fallback=new_request_id())


def normalize_correlation_id(value: object | None, *, fallback: str | None = None) -> str:
    return _normalize_context_value(
        value,
        fallback=fallback or current_request_id_or_new(),
    )


def normalize_task_id(value: object | None) -> str:
    return _normalize_context_value(value, fallback="-")


def current_request_id_or_new() -> str:
    return normalize_request_id(get_request_id())


def current_correlation_id_or_new() -> str:
    return normalize_correlation_id(get_correlation_id())


def current_task_id_or_none() -> str | None:
    task_id = normalize_task_id(get_task_id())
    return None if task_id == "-" else task_id


def request_id_from_headers(headers: Mapping[str, object] | None) -> str:
    if headers is None:
        return new_request_id()
    return normalize_request_id(headers.get(REQUEST_ID_HEADER))


def correlation_id_from_headers(
    headers: Mapping[str, object] | None,
    *,
    fallback: str | None = None,
) -> str:
    if headers is None:
        return normalize_correlation_id(None, fallback=fallback)
    return normalize_correlation_id(headers.get(CORRELATION_ID_HEADER), fallback=fallback)


def parent_task_id_from_headers(headers: Mapping[str, object] | None) -> str:
    if headers is None:
        return "-"
    return normalize_task_id(headers.get(PARENT_TASK_ID_HEADER))


def set_request_id(value: str) -> contextvars.Token[str]:
    return _request_id_ctx.set(value)


def set_correlation_id(value: str) -> contextvars.Token[str]:
    return _correlation_id_ctx.set(value)


def set_task_id(value: str) -> contextvars.Token[str]:
    return _task_id_ctx.set(value)


def set_parent_task_id(value: str) -> contextvars.Token[str]:
    return _parent_task_id_ctx.set(value)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id_ctx.reset(token)


def reset_correlation_id(token: contextvars.Token[str]) -> None:
    _correlation_id_ctx.reset(token)


def reset_task_id(token: contextvars.Token[str]) -> None:
    _task_id_ctx.reset(token)


def reset_parent_task_id(token: contextvars.Token[str]) -> None:
    _parent_task_id_ctx.reset(token)


@contextmanager
def bind_request_context(
    *,
    request_id: object | None,
    correlation_id: object | None = None,
    task_id: object | None = None,
    parent_task_id: object | None = None,
) -> Iterator[str]:
    normalized_request_id = normalize_request_id(request_id)
    normalized_correlation_id = normalize_correlation_id(
        correlation_id,
        fallback=normalized_request_id,
    )
    normalized_task_id = normalize_task_id(task_id)
    normalized_parent_task_id = normalize_task_id(parent_task_id)
    request_token = set_request_id(normalized_request_id)
    correlation_token = set_correlation_id(normalized_correlation_id)
    task_token = set_task_id(normalized_task_id)
    parent_task_token = set_parent_task_id(normalized_parent_task_id)
    try:
        yield normalized_request_id
    finally:
        reset_parent_task_id(parent_task_token)
        reset_task_id(task_token)
        reset_correlation_id(correlation_token)
        reset_request_id(request_token)


@contextmanager
def bind_request_id(value: object | None) -> Iterator[str]:
    with bind_request_context(request_id=value) as request_id:
        yield request_id


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = get_request_id()
        record.correlation_id = get_correlation_id()
        record.task_id = get_task_id()
        record.parent_task_id = get_parent_task_id()
        return True


RequestIdFilter = RequestContextFilter


def configure_request_id_logging() -> None:
    root = logging.getLogger()
    if getattr(root, _REQUEST_ID_LOGGING_FLAG, False):
        return
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format=_REQUEST_CONTEXT_LOG_FORMAT,
        )

    request_context_filter = RequestContextFilter()
    for handler in root.handlers:
        if not any(isinstance(existing, RequestContextFilter) for existing in handler.filters):
            handler.addFilter(request_context_filter)
        if handler.formatter is None:
            handler.setFormatter(logging.Formatter(_REQUEST_CONTEXT_LOG_FORMAT))
    setattr(root, _REQUEST_ID_LOGGING_FLAG, True)
