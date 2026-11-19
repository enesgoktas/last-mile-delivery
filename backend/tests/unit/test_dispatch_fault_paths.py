from __future__ import annotations

import asyncio
import uuid

import pytest

from app.models.enums import OptimizationRunStatus
from app.services import dispatch_service, optimization_control_service, route_scenario_service
from app.workers import optimization_tasks


class _RetryScheduled(Exception):
    pass


class _FakeRedis:
    def __init__(self, *, fail_get: bool = False, fail_set: bool = False):
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.values: dict[str, str] = {}
        self.released: list[tuple[str, str]] = []

    def get(self, key):
        if self.fail_get:
            raise RuntimeError("redis unavailable")
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        del ex
        if self.fail_set:
            raise RuntimeError("redis unavailable")
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script, num_keys, *args):
        if num_keys == 2:
            key, meta_key, owner = args
        else:
            key, owner = args
            meta_key = None
        if self.values.get(key) == owner:
            self.released.append((key, owner))
            del self.values[key]
            if meta_key is not None:
                self.values.pop(meta_key, None)
            return 1
        return 0


class _FakeRedisModule:
    def __init__(self, redis_client: _FakeRedis):
        self._redis_client = redis_client

    def from_url(self, *_args, **_kwargs):
        return self._redis_client


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _run_bound_task(task, *, retries: int = 0, task_id: str | None = None, **kwargs):
    task.push_request(id=task_id or f"fault-task-{uuid.uuid4().hex[:10]}", retries=retries)
    try:
        return task.run(**kwargs)
    finally:
        task.pop_request()


def _unexpected_session_factory():
    raise AssertionError("database access should not happen for OSRM availability failures")


@pytest.mark.asyncio
async def test_dispatch_batch_returns_osrm_unavailable_when_healthcheck_fails(monkeypatch):
    async def _false():
        return False

    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _false)
    monkeypatch.setattr(dispatch_service, "get_async_session_factory", _unexpected_session_factory)

    result = await dispatch_service.dispatch_batch()

    assert result["solver_status"] == "OSRM_UNAVAILABLE"
    assert result["routes_created"] == 0
    assert result["unassigned_count"] == 0
    assert result["unassigned_order_ids"] == []
    assert "dispatch_batch_latency_ms" in result


@pytest.mark.asyncio
async def test_dispatch_on_demand_returns_osrm_unavailable_when_healthcheck_fails(monkeypatch):
    async def _false():
        return False

    monkeypatch.setattr(dispatch_service.osrm_client, "check_osrm_health", _false)
    monkeypatch.setattr(dispatch_service, "get_async_session_factory", _unexpected_session_factory)

    result = await dispatch_service.dispatch_on_demand(str(uuid.uuid4()))

    assert result["result"] == "OSRM_UNAVAILABLE"
    assert "dispatch_on_demand_latency_ms" in result


def test_run_batch_optimization_records_failed_run_retries_and_releases_lock(monkeypatch):
    fake_redis = _FakeRedis()
    finish_calls: list[dict] = []
    retry_calls: list[Exception] = []

    async def _fake_start_run(*_args, **_kwargs):
        return "run-batch-failed"

    async def _fake_finish_run(**kwargs):
        finish_calls.append(kwargs)

    async def _fake_do_batch_optimization(*_args, **_kwargs):
        raise RuntimeError("solver exploded")

    def _fake_retry(*, exc, **_kwargs):
        retry_calls.append(exc)
        raise _RetryScheduled("retry requested")

    monkeypatch.setattr(optimization_tasks, "_run_async", _run_async_immediately)
    monkeypatch.setattr(optimization_tasks, "_start_run", _fake_start_run)
    monkeypatch.setattr(optimization_tasks, "_finish_run", _fake_finish_run)
    monkeypatch.setattr(optimization_tasks, "_do_batch_optimization", _fake_do_batch_optimization)
    monkeypatch.setattr(optimization_tasks, "Redis", _FakeRedisModule(fake_redis))
    monkeypatch.setattr(optimization_tasks.run_batch_optimization, "retry", _fake_retry)

    with pytest.raises(_RetryScheduled, match="retry requested"):
        _run_bound_task(
            optimization_tasks.run_batch_optimization,
            retries=0,
            draft=False,
            tenant_id=str(uuid.uuid4()),
        )

    assert len(retry_calls) == 1
    assert str(retry_calls[0]) == "solver exploded"
    assert finish_calls == [
        {
            "run_id": "run-batch-failed",
            "status_value": OptimizationRunStatus.FAILED,
            "error_message": "solver exploded",
        }
    ]
    assert fake_redis.values == {}
    assert len(fake_redis.released) == 1
    assert fake_redis.released[0][0] == "optimization:batch:lock"


def test_run_batch_optimization_exits_when_superseded_before_start(monkeypatch):
    task_id = f"superseded-batch-{uuid.uuid4().hex[:10]}"
    fake_redis = _FakeRedis()
    fake_redis.values[optimization_control_service.task_cancellation_key(task_id)] = (
        optimization_control_service.SUPERSEDED_OPTIMIZATION_MESSAGE
    )
    finish_calls: list[dict] = []

    async def _fake_start_run(*_args, **_kwargs):
        return "run-batch-superseded"

    async def _fake_finish_run(**kwargs):
        finish_calls.append(kwargs)

    async def _unexpected_batch_call(*_args, **_kwargs):
        raise AssertionError("superseded batch task should not run the solver")

    monkeypatch.setattr(optimization_tasks, "_run_async", _run_async_immediately)
    monkeypatch.setattr(optimization_tasks, "_start_run", _fake_start_run)
    monkeypatch.setattr(optimization_tasks, "_finish_run", _fake_finish_run)
    monkeypatch.setattr(optimization_tasks, "_do_batch_optimization", _unexpected_batch_call)
    monkeypatch.setattr(optimization_tasks, "Redis", _FakeRedisModule(fake_redis))

    result = _run_bound_task(
        optimization_tasks.run_batch_optimization,
        task_id=task_id,
        retries=0,
        draft=False,
        tenant_id=str(uuid.uuid4()),
    )

    assert result == {
        "routes_created": 0,
        "unassigned_count": 0,
        "solver_status": "SUPERSEDED",
    }
    assert finish_calls == [
        {
            "run_id": "run-batch-superseded",
            "status_value": OptimizationRunStatus.FAILED,
            "result": result,
            "error_message": optimization_control_service.SUPERSEDED_OPTIMIZATION_MESSAGE,
        }
    ]
    assert "optimization:batch:lock" not in fake_redis.values


def test_run_batch_optimization_releases_lock_on_terminal_failure(monkeypatch):
    fake_redis = _FakeRedis()
    finish_calls: list[dict] = []
    retry_calls: list[Exception] = []

    async def _fake_start_run(*_args, **_kwargs):
        return "run-batch-terminal"

    async def _fake_finish_run(**kwargs):
        finish_calls.append(kwargs)

    async def _fake_do_batch_optimization(*_args, **_kwargs):
        raise RuntimeError("solver exploded permanently")

    def _fake_retry(*, exc, **_kwargs):
        retry_calls.append(exc)
        return RuntimeError("unexpected retry")

    monkeypatch.setattr(optimization_tasks, "_run_async", _run_async_immediately)
    monkeypatch.setattr(optimization_tasks, "_start_run", _fake_start_run)
    monkeypatch.setattr(optimization_tasks, "_finish_run", _fake_finish_run)
    monkeypatch.setattr(optimization_tasks, "_do_batch_optimization", _fake_do_batch_optimization)
    monkeypatch.setattr(optimization_tasks, "Redis", _FakeRedisModule(fake_redis))
    monkeypatch.setattr(optimization_tasks.run_batch_optimization, "retry", _fake_retry)

    with pytest.raises(RuntimeError, match="solver exploded permanently"):
        _run_bound_task(
            optimization_tasks.run_batch_optimization,
            retries=optimization_tasks.run_batch_optimization.max_retries,
            draft=False,
            tenant_id=str(uuid.uuid4()),
        )

    assert retry_calls == []
    assert finish_calls == [
        {
            "run_id": "run-batch-terminal",
            "status_value": OptimizationRunStatus.FAILED,
            "error_message": "solver exploded permanently",
        }
    ]
    assert fake_redis.values == {}
    assert len(fake_redis.released) == 1
    assert fake_redis.released[0][0] == "optimization:batch:lock"


def test_run_route_scenario_comparison_marks_failed_on_uncaught_error(monkeypatch):
    fake_redis = _FakeRedis()
    failure_marks: list[dict] = []

    async def _fake_run_comparison(*_args, **_kwargs):
        raise RuntimeError("scenario exploded")

    async def _fake_mark_failed(**kwargs):
        failure_marks.append(kwargs)

    monkeypatch.setattr(optimization_tasks, "_run_async", _run_async_immediately)
    monkeypatch.setattr(route_scenario_service, "run_comparison", _fake_run_comparison)
    monkeypatch.setattr(
        optimization_tasks,
        "_mark_route_scenario_comparison_failed",
        _fake_mark_failed,
    )
    monkeypatch.setattr(optimization_tasks, "Redis", _FakeRedisModule(fake_redis))

    comparison_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    with pytest.raises(RuntimeError, match="scenario exploded"):
        _run_bound_task(
            optimization_tasks.run_route_scenario_comparison,
            retries=0,
            comparison_id=comparison_id,
            tenant_id=tenant_id,
            target_date_iso="2026-05-05",
        )

    assert failure_marks == [
        {
            "comparison_id": comparison_id,
            "tenant_id": tenant_id,
            "error_message": "scenario exploded",
        }
    ]
    assert fake_redis.values == {}
    assert len(fake_redis.released) == 1
    assert fake_redis.released[0][0] == "optimization:batch:lock"


def test_run_reoptimization_proceeds_without_guardrails_when_redis_is_unavailable(monkeypatch):
    finish_calls: list[dict] = []
    fake_redis = _FakeRedis(fail_get=True)

    async def _fake_start_run(*_args, **_kwargs):
        return "run-reopt-guardless"

    async def _fake_finish_run(**kwargs):
        finish_calls.append(kwargs)

    async def _fake_do_reoptimization(*_args, **_kwargs):
        return {"routes_aborted": 1, "orders_requeued": 2}

    def _unexpected_release(*_args, **_kwargs):
        raise AssertionError("guardless re-optimization should not try to release a Redis lock")

    def _unexpected_cooldown(*_args, **_kwargs):
        raise AssertionError("guardless re-optimization should not try to set a cooldown")

    monkeypatch.setattr(optimization_tasks, "_run_async", _run_async_immediately)
    monkeypatch.setattr(optimization_tasks, "_start_run", _fake_start_run)
    monkeypatch.setattr(optimization_tasks, "_finish_run", _fake_finish_run)
    monkeypatch.setattr(optimization_tasks, "_do_reoptimization", _fake_do_reoptimization)
    monkeypatch.setattr(optimization_tasks, "_release_owned_lock", _unexpected_release)
    monkeypatch.setattr(optimization_tasks, "_set_reoptimization_cooldown", _unexpected_cooldown)
    monkeypatch.setattr(optimization_tasks, "Redis", _FakeRedisModule(fake_redis))

    result = _run_bound_task(
        optimization_tasks.run_reoptimization,
        retries=0,
        courier_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
    )

    assert result == {"routes_aborted": 1, "orders_requeued": 2}
    assert finish_calls == [
        {
            "run_id": "run-reopt-guardless",
            "status_value": OptimizationRunStatus.SUCCEEDED,
            "result": {"routes_aborted": 1, "orders_requeued": 2},
        }
    ]


def test_run_reoptimization_records_failed_run_retries_and_releases_lock(monkeypatch):
    fake_redis = _FakeRedis()
    finish_calls: list[dict] = []
    retry_calls: list[Exception] = []

    async def _fake_start_run(*_args, **_kwargs):
        return "run-reopt-failed"

    async def _fake_finish_run(**kwargs):
        finish_calls.append(kwargs)

    async def _fake_do_reoptimization(*_args, **_kwargs):
        raise RuntimeError("reoptimization exploded")

    def _fake_retry(*, exc, **_kwargs):
        retry_calls.append(exc)
        raise _RetryScheduled("retry reoptimization")

    monkeypatch.setattr(optimization_tasks, "_run_async", _run_async_immediately)
    monkeypatch.setattr(optimization_tasks, "_start_run", _fake_start_run)
    monkeypatch.setattr(optimization_tasks, "_finish_run", _fake_finish_run)
    monkeypatch.setattr(optimization_tasks, "_do_reoptimization", _fake_do_reoptimization)
    monkeypatch.setattr(optimization_tasks, "Redis", _FakeRedisModule(fake_redis))
    monkeypatch.setattr(optimization_tasks.run_reoptimization, "retry", _fake_retry)

    with pytest.raises(_RetryScheduled, match="retry reoptimization"):
        _run_bound_task(
            optimization_tasks.run_reoptimization,
            retries=0,
            courier_id="courier-123",
            tenant_id=str(uuid.uuid4()),
        )

    assert len(retry_calls) == 1
    assert str(retry_calls[0]) == "reoptimization exploded"
    assert finish_calls == [
        {
            "run_id": "run-reopt-failed",
            "status_value": OptimizationRunStatus.FAILED,
            "error_message": "reoptimization exploded",
        }
    ]
    assert fake_redis.values == {}
    assert len(fake_redis.released) == 1
    assert fake_redis.released[0][0] == "optimization:reopt:courier-123:lock"
