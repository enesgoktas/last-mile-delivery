import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import ProgrammingError

from app.utils.secret_encryption import decrypt_secret, encrypt_secret
from app.utils.secret_rotation import rotate_encrypted_value


def _load_rotation_script():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "rotate_encrypted_secrets.py"
    spec = importlib.util.spec_from_file_location("rotate_encrypted_secrets", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load rotate_encrypted_secrets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


rotate_encrypted_secrets = _load_rotation_script()


def test_rotate_encrypted_value_reencrypts_with_new_seed():
    original = encrypt_secret("rotate-me", seed="old-seed")

    rotated = rotate_encrypted_value(
        original,
        old_seed="old-seed",
        new_seed="new-seed",
    )

    assert rotated != original
    assert decrypt_secret(rotated, seed="new-seed") == "rotate-me"
    with pytest.raises(ValueError, match="Invalid encrypted secret"):
        decrypt_secret(rotated, seed="old-seed")


def test_rotate_encrypted_value_migrates_plaintext_legacy_secret():
    rotated = rotate_encrypted_value(
        "legacy-plaintext-secret",
        old_seed="old-seed",
        new_seed="new-seed",
    )

    assert rotated.startswith("enc::")
    assert decrypt_secret(rotated, seed="new-seed") == "legacy-plaintext-secret"


def test_rotate_encrypted_value_rejects_equal_seeds():
    with pytest.raises(ValueError, match="must differ"):
        rotate_encrypted_value(
            encrypt_secret("same", seed="seed-one"),
            old_seed="seed-one",
            new_seed="seed-one",
        )


def test_format_schema_mismatch_error_is_actionable():
    error = ProgrammingError("SELECT 1", {}, Exception("column missing"))

    message = rotate_encrypted_secrets._format_schema_mismatch_error(
        target="Webhook",
        exc=error,
    )

    assert "Webhook secret rotation failed" in message
    assert "Run database migrations" in message
    assert "column missing" in message


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> _FakeSessionContext:
        return _FakeSessionContext(self._session)


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_rotate_secrets_commits_when_apply_changes(monkeypatch: pytest.MonkeyPatch):
    engine = _FakeEngine()
    session = _FakeSession()
    observed: dict[str, object] = {}

    async def fake_rotate_webhooks(db, *, old_seed, new_seed, apply_changes):
        observed["webhooks"] = (db, old_seed, new_seed, apply_changes)
        return 2

    async def fake_rotate_oidc(db, *, old_seed, new_seed, apply_changes):
        observed["oidc"] = (db, old_seed, new_seed, apply_changes)
        return 5

    monkeypatch.setattr(rotate_encrypted_secrets, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        rotate_encrypted_secrets,
        "async_sessionmaker",
        lambda *_args, **_kwargs: _FakeSessionFactory(session),
    )
    monkeypatch.setattr(rotate_encrypted_secrets, "_rotate_webhook_secrets", fake_rotate_webhooks)
    monkeypatch.setattr(rotate_encrypted_secrets, "_rotate_oidc_client_secrets", fake_rotate_oidc)

    counts = await rotate_encrypted_secrets.rotate_secrets(
        database_url="sqlite+aiosqlite://",
        old_seed="old-seed",
        new_seed="new-seed",
        rotate_webhooks=True,
        rotate_oidc=False,
        apply_changes=True,
    )

    assert counts == rotate_encrypted_secrets.RotationCounts(webhook_endpoints=2, oidc_providers=0)
    assert observed["webhooks"] == (session, "old-seed", "new-seed", True)
    assert "oidc" not in observed
    assert session.committed is True
    assert session.rolled_back is False
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_rotate_secrets_rolls_back_dry_run(monkeypatch: pytest.MonkeyPatch):
    engine = _FakeEngine()
    session = _FakeSession()
    observed: dict[str, object] = {}

    async def fake_rotate_webhooks(db, *, old_seed, new_seed, apply_changes):
        observed["webhooks"] = (db, old_seed, new_seed, apply_changes)
        return 1

    async def fake_rotate_oidc(db, *, old_seed, new_seed, apply_changes):
        observed["oidc"] = (db, old_seed, new_seed, apply_changes)
        return 3

    monkeypatch.setattr(rotate_encrypted_secrets, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        rotate_encrypted_secrets,
        "async_sessionmaker",
        lambda *_args, **_kwargs: _FakeSessionFactory(session),
    )
    monkeypatch.setattr(rotate_encrypted_secrets, "_rotate_webhook_secrets", fake_rotate_webhooks)
    monkeypatch.setattr(rotate_encrypted_secrets, "_rotate_oidc_client_secrets", fake_rotate_oidc)

    counts = await rotate_encrypted_secrets.rotate_secrets(
        database_url="sqlite+aiosqlite://",
        old_seed="old-seed",
        new_seed="new-seed",
        rotate_webhooks=True,
        rotate_oidc=True,
        apply_changes=False,
    )

    assert counts == rotate_encrypted_secrets.RotationCounts(webhook_endpoints=1, oidc_providers=3)
    assert observed["webhooks"] == (session, "old-seed", "new-seed", False)
    assert observed["oidc"] == (session, "old-seed", "new-seed", False)
    assert session.committed is False
    assert session.rolled_back is True
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_rotate_secrets_raises_actionable_error_on_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch,
):
    engine = _FakeEngine()
    session = _FakeSession()

    async def failing_rotate_webhooks(_db, *, old_seed, new_seed, apply_changes):
        raise ProgrammingError(
            "SELECT webhook_endpoints.id FROM webhook_endpoints",
            {},
            Exception("column webhook_endpoints.tenant_id does not exist"),
        )

    monkeypatch.setattr(rotate_encrypted_secrets, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        rotate_encrypted_secrets,
        "async_sessionmaker",
        lambda *_args, **_kwargs: _FakeSessionFactory(session),
    )
    monkeypatch.setattr(rotate_encrypted_secrets, "_rotate_webhook_secrets", failing_rotate_webhooks)

    with pytest.raises(RuntimeError, match="Run database migrations"):
        await rotate_encrypted_secrets.rotate_secrets(
            database_url="sqlite+aiosqlite://",
            old_seed="old-seed",
            new_seed="new-seed",
            rotate_webhooks=True,
            rotate_oidc=False,
            apply_changes=False,
        )

    assert engine.disposed is True
