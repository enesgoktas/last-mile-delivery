import logging

from app.utils import request_context


def test_normalize_request_id_trims_input_and_generates_fallback(monkeypatch):
    monkeypatch.setattr(request_context, "new_request_id", lambda: "generated-request-id")

    assert request_context.normalize_request_id("  req-123  ") == "req-123"
    assert request_context.normalize_request_id("   ") == "generated-request-id"
    assert request_context.normalize_request_id("-") == "generated-request-id"
    assert request_context.normalize_request_id(None) == "generated-request-id"


def test_request_id_from_headers_prefers_header_and_falls_back(monkeypatch):
    monkeypatch.setattr(request_context, "new_request_id", lambda: "generated-from-headers")

    assert request_context.request_id_from_headers(
        {request_context.REQUEST_ID_HEADER: "header-req-1"}
    ) == "header-req-1"
    assert request_context.request_id_from_headers({}) == "generated-from-headers"
    assert request_context.request_id_from_headers(None) == "generated-from-headers"


def test_correlation_id_from_headers_prefers_header_then_request_id():
    assert request_context.correlation_id_from_headers(
        {request_context.CORRELATION_ID_HEADER: "corr-1"},
        fallback="req-1",
    ) == "corr-1"
    assert request_context.correlation_id_from_headers({}, fallback="req-1") == "req-1"


def test_bind_request_id_restores_previous_context():
    outer_token = request_context.set_request_id("outer-request-id")
    outer_correlation_token = request_context.set_correlation_id("outer-correlation-id")
    try:
        with request_context.bind_request_id("  inner-request-id  ") as bound_request_id:
            assert bound_request_id == "inner-request-id"
            assert request_context.get_request_id() == "inner-request-id"
            assert request_context.get_correlation_id() == "inner-request-id"
            assert request_context.get_task_id() == "-"
            assert request_context.get_parent_task_id() == "-"

        assert request_context.get_request_id() == "outer-request-id"
        assert request_context.get_correlation_id() == "outer-correlation-id"
    finally:
        request_context.reset_correlation_id(outer_correlation_token)
        request_context.reset_request_id(outer_token)


def test_request_id_filter_injects_context_value():
    token = request_context.set_request_id("req-123")
    correlation_token = request_context.set_correlation_id("corr-123")
    task_token = request_context.set_task_id("task-123")
    parent_task_token = request_context.set_parent_task_id("parent-task-123")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="message",
            args=(),
            exc_info=None,
        )

        result = request_context.RequestIdFilter().filter(record)

        assert result is True
        assert record.request_id == "req-123"
        assert record.correlation_id == "corr-123"
        assert record.task_id == "task-123"
        assert record.parent_task_id == "parent-task-123"
    finally:
        request_context.reset_parent_task_id(parent_task_token)
        request_context.reset_task_id(task_token)
        request_context.reset_correlation_id(correlation_token)
        request_context.reset_request_id(token)


def test_configure_request_id_logging_is_idempotent():
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_flag = getattr(root, request_context._REQUEST_ID_LOGGING_FLAG, False)

    handler = logging.StreamHandler()
    try:
        root.handlers = [handler]
        setattr(root, request_context._REQUEST_ID_LOGGING_FLAG, False)

        request_context.configure_request_id_logging()
        request_context.configure_request_id_logging()

        filters = [f for f in handler.filters if isinstance(f, request_context.RequestIdFilter)]
        assert len(filters) == 1
        assert handler.formatter is not None
        assert "%(request_id)s" in handler.formatter._fmt
        assert "%(correlation_id)s" in handler.formatter._fmt
        assert "%(task_id)s" in handler.formatter._fmt
        assert "%(parent_task_id)s" in handler.formatter._fmt
    finally:
        root.handlers = original_handlers
        setattr(root, request_context._REQUEST_ID_LOGGING_FLAG, original_flag)
