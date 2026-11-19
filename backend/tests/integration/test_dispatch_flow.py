import uuid
from types import SimpleNamespace

from app.schemas.operations_policy import default_operations_policy
from tests.integration.test_utils import build_test_client, find_route_dependency


def test_dispatch_optimize_enqueues_batch_task(monkeypatch):
    from app.api import dispatch as dispatch_api

    role_dep = find_route_dependency("/api/v1/dispatch/optimize", "current_user")
    tenant_id = uuid.uuid4()

    async def _override_role():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="DISPATCHER", is_active=True)

    class _MockTask:
        id = "task_batch_123"

    captured = {"kwargs": None}

    def _capture_enqueue(_task, *, kwargs=None, **_extra):
        captured["kwargs"] = kwargs
        return _MockTask()

    async def _mock_get_operations_policy(*_args, **_kwargs):
        return default_operations_policy()

    monkeypatch.setattr(dispatch_api, "enqueue_task", _capture_enqueue)
    monkeypatch.setattr(
        dispatch_api.operations_policy_service,
        "get_operations_policy",
        _mock_get_operations_policy,
    )
    with build_test_client(dependency_overrides={role_dep: _override_role}) as client:
        response = client.post("/api/v1/dispatch/optimize")

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == "task_batch_123"
    assert body["message"] == "Batch optimization task enqueued"
    assert captured["kwargs"] == {"tenant_id": str(tenant_id), "draft": False}


def test_dispatch_reoptimize_enqueues_courier_specific_task(monkeypatch):
    from app.api import dispatch as dispatch_api

    role_dep = find_route_dependency("/api/v1/dispatch/reoptimize", "current_user")
    tenant_id = uuid.uuid4()

    async def _override_role():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="ADMIN", is_active=True)

    class _MockTask:
        id = "task_reopt_789"

    captured = {"args": None}

    def _capture_enqueue(_task, *, args=None, **_extra):
        captured["args"] = tuple(args or [])
        return _MockTask()

    monkeypatch.setattr(dispatch_api, "enqueue_task", _capture_enqueue)
    courier_id = uuid.uuid4()

    with build_test_client(dependency_overrides={role_dep: _override_role}) as client:
        response = client.post(
            "/api/v1/dispatch/reoptimize",
            json={"courier_id": str(courier_id)},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == "task_reopt_789"
    assert str(courier_id) in body["message"]
    assert captured["args"] == (str(courier_id), str(tenant_id))


def test_dispatch_status_normalizes_non_dict_results(monkeypatch):
    from app.api import dispatch as dispatch_api

    role_dep = find_route_dependency("/api/v1/dispatch/optimize/{task_id}/status", "current_user")
    tenant_id = uuid.uuid4()

    async def _override_role():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="DISPATCHER", is_active=True)

    class _MockAsyncResult:
        status = "FAILURE"
        result = RuntimeError("solver failed")

        @staticmethod
        def ready():
            return True

    class _MockCeleryApp:
        @staticmethod
        def AsyncResult(_task_id):
            return _MockAsyncResult()

    monkeypatch.setattr(dispatch_api, "celery_app", _MockCeleryApp)

    with build_test_client(dependency_overrides={role_dep: _override_role}) as client:
        response = client.get("/api/v1/dispatch/optimize/task_123/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["result"] == {"error": "solver failed"}
