import pytest

from app import database


class _FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self._in_transaction = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def commit(self) -> None:
        self.commit_calls += 1
        self._in_transaction = False

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self._in_transaction = False


def _override_session_factory(fake_session: _FakeSession):
    def _factory():
        return fake_session

    return _factory


@pytest.mark.asyncio
async def test_get_db_never_commits_implicitly(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(
        database,
        "get_async_session_factory",
        lambda: _override_session_factory(fake_session),
    )

    dependency = database.get_db()
    yielded = await anext(dependency)
    assert yielded is fake_session

    fake_session._in_transaction = True

    with pytest.raises(StopAsyncIteration):
        await anext(dependency)

    assert fake_session.commit_calls == 0
    assert fake_session.rollback_calls == 1


@pytest.mark.asyncio
async def test_get_write_db_commits_on_success(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(
        database,
        "get_async_session_factory",
        lambda: _override_session_factory(fake_session),
    )

    dependency = database.get_write_db()
    yielded = await anext(dependency)
    assert yielded is fake_session

    fake_session._in_transaction = True

    with pytest.raises(StopAsyncIteration):
        await anext(dependency)

    assert fake_session.commit_calls == 1
    assert fake_session.rollback_calls == 0


@pytest.mark.asyncio
async def test_get_write_db_rolls_back_on_exception(monkeypatch):
    fake_session = _FakeSession()
    monkeypatch.setattr(
        database,
        "get_async_session_factory",
        lambda: _override_session_factory(fake_session),
    )

    dependency = database.get_write_db()
    yielded = await anext(dependency)
    assert yielded is fake_session

    fake_session._in_transaction = True

    with pytest.raises(RuntimeError, match="boom"):
        await dependency.athrow(RuntimeError("boom"))

    assert fake_session.commit_calls == 0
    assert fake_session.rollback_calls == 1
