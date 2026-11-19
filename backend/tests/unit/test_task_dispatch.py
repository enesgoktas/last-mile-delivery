from types import SimpleNamespace

from app.utils import request_context
from app.workers import task_dispatch, task_request_context


def test_current_task_headers_use_current_request_id():
    token = request_context.set_request_id("req-current-123")
    correlation_token = request_context.set_correlation_id("corr-current-123")
    try:
        assert task_dispatch.current_task_headers() == {
            request_context.REQUEST_ID_HEADER: "req-current-123",
            request_context.CORRELATION_ID_HEADER: "corr-current-123",
        }
    finally:
        request_context.reset_correlation_id(correlation_token)
        request_context.reset_request_id(token)


def test_current_task_headers_include_parent_task_id_when_worker_context_exists():
    request_token = request_context.set_request_id("req-current-123")
    correlation_token = request_context.set_correlation_id("corr-current-123")
    task_token = request_context.set_task_id("task-current-789")
    try:
        assert task_dispatch.current_task_headers() == {
            request_context.REQUEST_ID_HEADER: "req-current-123",
            request_context.CORRELATION_ID_HEADER: "corr-current-123",
            request_context.PARENT_TASK_ID_HEADER: "task-current-789",
        }
    finally:
        request_context.reset_task_id(task_token)
        request_context.reset_correlation_id(correlation_token)
        request_context.reset_request_id(request_token)


def test_enqueue_task_passes_request_headers_and_options(monkeypatch):
    monkeypatch.setattr(
        task_dispatch,
        "current_task_headers",
        lambda: {
            request_context.REQUEST_ID_HEADER: "req-enqueue-123",
            request_context.CORRELATION_ID_HEADER: "corr-enqueue-123",
        },
    )
    captured: dict[str, object] = {}

    class _DummyTask:
        def apply_async(self, **options):
            captured.update(options)
            return SimpleNamespace(id="task-123")

    result = task_dispatch.enqueue_task(
        _DummyTask(),
        args=["arg-1", "arg-2"],
        kwargs={"draft": True},
        countdown=15,
    )

    assert result.id == "task-123"
    assert captured == {
        "headers": {
            request_context.REQUEST_ID_HEADER: "req-enqueue-123",
            request_context.CORRELATION_ID_HEADER: "corr-enqueue-123",
        },
        "args": ["arg-1", "arg-2"],
        "kwargs": {"draft": True},
        "countdown": 15,
    }


def test_bind_task_request_id_uses_task_headers_and_restores_context():
    outer_token = request_context.set_request_id("req-outer-123")
    outer_correlation_token = request_context.set_correlation_id("corr-outer-123")
    try:
        task_request = SimpleNamespace(
            id="task-child-789",
            headers={
                request_context.REQUEST_ID_HEADER: "req-task-456",
                request_context.CORRELATION_ID_HEADER: "corr-root-123",
                request_context.PARENT_TASK_ID_HEADER: "task-parent-123",
            },
        )

        with task_request_context.bind_task_request_id(task_request) as bound_request_id:
            assert bound_request_id == "req-task-456"
            assert request_context.get_request_id() == "req-task-456"
            assert request_context.get_correlation_id() == "corr-root-123"
            assert request_context.get_task_id() == "task-child-789"
            assert request_context.get_parent_task_id() == "task-parent-123"

        assert request_context.get_request_id() == "req-outer-123"
        assert request_context.get_correlation_id() == "corr-outer-123"
    finally:
        request_context.reset_correlation_id(outer_correlation_token)
        request_context.reset_request_id(outer_token)


def test_bind_task_request_id_generates_request_id_when_headers_missing(monkeypatch):
    monkeypatch.setattr(
        task_request_context,
        "request_id_from_headers",
        lambda _headers: "generated-task-request-id",
    )
    monkeypatch.setattr(
        task_request_context,
        "correlation_id_from_headers",
        lambda _headers, *, fallback: fallback or "generated-correlation-id",
    )

    with task_request_context.bind_task_request_id(
        SimpleNamespace(id="task-generated-123", headers=None)
    ) as bound_request_id:
        assert bound_request_id == "generated-task-request-id"
        assert request_context.get_request_id() == "generated-task-request-id"
        assert request_context.get_correlation_id() == "generated-task-request-id"
        assert request_context.get_task_id() == "task-generated-123"
        assert request_context.get_parent_task_id() == "-"
