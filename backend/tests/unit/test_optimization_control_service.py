from __future__ import annotations

import uuid

from app.services import optimization_control_service


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script, num_keys, *args):
        assert num_keys == 2
        key, meta_key, owner = args
        if self.values.get(key) != owner:
            return 0
        self.values.pop(meta_key, None)
        self.values.pop(key, None)
        return 1


def test_supersede_active_batch_optimization_revokes_task_and_clears_owned_lock(monkeypatch):
    redis_client = _FakeRedis()
    tenant_id = str(uuid.uuid4())
    revoke_calls: list[dict] = []

    def _fake_revoke(task_id, *, terminate, signal):
        revoke_calls.append(
            {
                "task_id": task_id,
                "terminate": terminate,
                "signal": signal,
            }
        )

    monkeypatch.setattr(optimization_control_service.celery_app.control, "revoke", _fake_revoke)
    acquired = optimization_control_service.acquire_batch_optimization_lock(
        redis_client,
        owner="owner-1",
        task_id="task-1",
        tenant_id=tenant_id,
        operation="BATCH",
        ttl_seconds=300,
    )

    result = optimization_control_service.supersede_active_batch_optimization_with_client(
        redis_client,
        tenant_id=tenant_id,
    )

    assert acquired is True
    assert result.cancelled is True
    assert result.task_id == "task-1"
    assert result.operation == "BATCH"
    assert revoke_calls == [
        {"task_id": "task-1", "terminate": True, "signal": "SIGTERM"}
    ]
    assert optimization_control_service.BATCH_OPTIMIZATION_LOCK_KEY not in redis_client.values
    assert optimization_control_service.BATCH_OPTIMIZATION_LOCK_META_KEY not in redis_client.values
    assert (
        redis_client.values[optimization_control_service.task_cancellation_key("task-1")]
        == optimization_control_service.SUPERSEDED_OPTIMIZATION_MESSAGE
    )


def test_supersede_active_batch_optimization_blocks_without_metadata(monkeypatch):
    redis_client = _FakeRedis()
    redis_client.values[optimization_control_service.BATCH_OPTIMIZATION_LOCK_KEY] = "legacy-owner"
    revoke_calls: list[str] = []

    monkeypatch.setattr(
        optimization_control_service.celery_app.control,
        "revoke",
        lambda task_id, **_kwargs: revoke_calls.append(task_id),
    )

    result = optimization_control_service.supersede_active_batch_optimization_with_client(
        redis_client,
        tenant_id=str(uuid.uuid4()),
    )

    assert result.cancelled is False
    assert result.blocked is True
    assert revoke_calls == []
    assert redis_client.values[optimization_control_service.BATCH_OPTIMIZATION_LOCK_KEY] == "legacy-owner"


def test_supersede_active_batch_optimization_revokes_queued_task_before_lock(monkeypatch):
    redis_client = _FakeRedis()
    tenant_id = str(uuid.uuid4())
    revoke_calls: list[str] = []

    monkeypatch.setattr(
        optimization_control_service.celery_app.control,
        "revoke",
        lambda task_id, **_kwargs: revoke_calls.append(task_id),
    )
    optimization_control_service.record_active_batch_optimization_task_with_client(
        redis_client,
        task_id="queued-task-1",
        tenant_id=tenant_id,
        operation="ROUTE_SCENARIO_COMPARISON",
        comparison_id=str(uuid.uuid4()),
    )

    result = optimization_control_service.supersede_active_batch_optimization_with_client(
        redis_client,
        tenant_id=tenant_id,
    )

    assert result.cancelled is True
    assert result.task_id == "queued-task-1"
    assert result.operation == "ROUTE_SCENARIO_COMPARISON"
    assert revoke_calls == ["queued-task-1"]
    assert optimization_control_service.BATCH_OPTIMIZATION_ACTIVE_TASK_KEY not in redis_client.values
    assert optimization_control_service.BATCH_OPTIMIZATION_ACTIVE_TASK_META_KEY not in redis_client.values


def test_supersede_active_batch_optimization_does_not_cancel_other_tenant(monkeypatch):
    redis_client = _FakeRedis()
    revoke_calls: list[str] = []

    monkeypatch.setattr(
        optimization_control_service.celery_app.control,
        "revoke",
        lambda task_id, **_kwargs: revoke_calls.append(task_id),
    )
    optimization_control_service.acquire_batch_optimization_lock(
        redis_client,
        owner="owner-1",
        task_id="task-1",
        tenant_id=str(uuid.uuid4()),
        operation="BATCH",
        ttl_seconds=300,
    )

    result = optimization_control_service.supersede_active_batch_optimization_with_client(
        redis_client,
        tenant_id=str(uuid.uuid4()),
    )

    assert result.cancelled is False
    assert result.blocked is True
    assert revoke_calls == []
    assert redis_client.values[optimization_control_service.BATCH_OPTIMIZATION_LOCK_KEY] == "owner-1"
