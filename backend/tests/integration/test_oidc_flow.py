import uuid
from datetime import datetime
from types import SimpleNamespace

from fastapi import status

from app.models.enums import UserRole
from app.models.oidc_provider import OIDCProvider
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.compat import UTC
from tests.integration.test_utils import build_test_client, find_route_dependency


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _extract_filter_value(query, column_name: str):
    whereclause = getattr(query, "whereclause", None)
    if whereclause is None:
        return None

    clauses = list(getattr(whereclause, "clauses", [whereclause]))
    for clause in clauses:
        left = getattr(clause, "left", None)
        if getattr(left, "name", None) != column_name:
            continue
        right = getattr(clause, "right", None)
        if hasattr(right, "value"):
            return right.value
    return None


class _FakeDBSession:
    def __init__(
        self,
        *,
        providers: list[OIDCProvider] | None = None,
        tenants: list[Tenant] | None = None,
    ) -> None:
        self.providers_by_tenant_id: dict[uuid.UUID, OIDCProvider] = {}
        self.tenants_by_slug: dict[str, Tenant] = {}
        for tenant in tenants or []:
            self.tenants_by_slug[tenant.slug] = tenant
        for provider in providers or []:
            self.providers_by_tenant_id[provider.tenant_id] = provider

    async def execute(self, query):
        slug = _extract_filter_value(query, "slug")
        if slug is not None:
            tenant = self.tenants_by_slug.get(slug)
            if tenant is None:
                return _ScalarResult(None)
            provider = self.providers_by_tenant_id.get(tenant.id)
            if provider is not None and provider.is_enabled:
                return _ScalarResult(provider)
            return _ScalarResult(None)

        tenant_id = _extract_filter_value(query, "tenant_id")
        if tenant_id is not None:
            return _ScalarResult(self.providers_by_tenant_id.get(tenant_id))

        return _ScalarResult(None)

    def add(self, obj):
        if isinstance(obj, OIDCProvider):
            if obj.id is None:
                obj.id = uuid.uuid4()
            if obj.created_at is None:
                obj.created_at = datetime.now(UTC).replace(tzinfo=None)
            if obj.updated_at is None:
                obj.updated_at = obj.created_at
            if obj.is_enabled is None:
                obj.is_enabled = True
            self.providers_by_tenant_id[obj.tenant_id] = obj
        elif isinstance(obj, Tenant):
            self.tenants_by_slug[obj.slug] = obj

    async def delete(self, obj):
        if isinstance(obj, OIDCProvider):
            self.providers_by_tenant_id.pop(obj.tenant_id, None)

    async def flush(self):
        return None


def _make_provider(*, tenant_id: uuid.UUID, client_secret_enc: str) -> OIDCProvider:
    now = datetime.now(UTC).replace(tzinfo=None)
    return OIDCProvider(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        issuer="https://idp.example.com",
        client_id="client-123",
        client_secret_enc=client_secret_enc,
        redirect_uri="https://app.example.com/oidc/callback",
        scopes="openid email profile",
        role_claim="lmd_role",
        default_role="DISPATCHER",
        is_enabled=True,
        created_at=now,
        updated_at=now,
    )


def _make_tenant(*, tenant_id: uuid.UUID, slug: str) -> Tenant:
    return Tenant(
        id=tenant_id,
        name="Acme",
        slug=slug,
        is_active=True,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )


def _make_admin(*, tenant_id: uuid.UUID):
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role=UserRole.ADMIN, is_active=True)


def test_configure_oidc_encrypts_secret_and_returns_metadata(monkeypatch):
    from app.api import oidc as oidc_api

    tenant_id = uuid.uuid4()
    fake_db = _FakeDBSession()
    role_dep = find_route_dependency("/api/v1/auth/oidc/configure", "_admin")

    async def _override_admin():
        return _make_admin(tenant_id=tenant_id)

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(oidc_api, "log_audit_event", _noop_audit)
    monkeypatch.setattr(oidc_api.settings, "OIDC_SECRET_ENCRYPTION_KEY", "oidc-seed")

    with build_test_client(db_session=fake_db, dependency_overrides={role_dep: _override_admin}) as client:
        response = client.post(
            "/api/v1/auth/oidc/configure",
            headers={"Authorization": "Bearer admin-token"},
            json={
                "issuer": "https://idp.example.com",
                "client_id": "client-123",
                "client_secret": "super-secret",
                "redirect_uri": "https://app.example.com/oidc/callback",
                "scopes": ["openid", "email", "profile"],
                "role_claim": "lmd_role",
                "default_role": "DISPATCHER",
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["client_id"] == "client-123"
    stored_provider = fake_db.providers_by_tenant_id[tenant_id]
    assert stored_provider.client_secret_enc != "super-secret"
    assert stored_provider.client_secret_enc.startswith("enc::")
    assert oidc_api._decrypt_secret(stored_provider.client_secret_enc) == "super-secret"


def test_get_oidc_configuration_returns_provider(monkeypatch):
    tenant_id = uuid.uuid4()
    provider = _make_provider(tenant_id=tenant_id, client_secret_enc="enc::placeholder")
    fake_db = _FakeDBSession(providers=[provider])
    role_dep = find_route_dependency("/api/v1/auth/oidc/configure", "_admin")

    async def _override_admin():
        return _make_admin(tenant_id=tenant_id)

    with build_test_client(db_session=fake_db, dependency_overrides={role_dep: _override_admin}) as client:
        response = client.get(
            "/api/v1/auth/oidc/configure",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["issuer"] == provider.issuer
    assert body["redirect_uri"] == provider.redirect_uri
    assert body["scopes"] == ["openid", "email", "profile"]


def test_delete_oidc_configuration_removes_provider(monkeypatch):
    from app.api import oidc as oidc_api

    tenant_id = uuid.uuid4()
    provider = _make_provider(tenant_id=tenant_id, client_secret_enc="enc::placeholder")
    fake_db = _FakeDBSession(providers=[provider])
    role_dep = find_route_dependency("/api/v1/auth/oidc/configure", "_admin")

    async def _override_admin():
        return _make_admin(tenant_id=tenant_id)

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(oidc_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=fake_db, dependency_overrides={role_dep: _override_admin}) as client:
        response = client.delete(
            "/api/v1/auth/oidc/configure",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert tenant_id not in fake_db.providers_by_tenant_id


def test_oidc_login_stores_state_and_returns_authorization_url(monkeypatch):
    from app.api import oidc as oidc_api

    tenant_id = uuid.uuid4()
    monkeypatch.setattr(oidc_api.settings, "OIDC_SECRET_ENCRYPTION_KEY", "oidc-seed")
    fake_db = _FakeDBSession(
        providers=[
            _make_provider(
                tenant_id=tenant_id,
                client_secret_enc=oidc_api._encrypt_secret("super-secret"),
            )
        ],
        tenants=[_make_tenant(tenant_id=tenant_id, slug="acme")],
    )
    captured: dict[str, str] = {}

    async def _mock_build_authorization_url(_config, state: str):
        captured["auth_state"] = state
        return f"https://idp.example.com/authorize?state={state}"

    async def _capture_store_oidc_state(*, state: str, tenant_id: str) -> None:
        captured["stored_state"] = state
        captured["tenant_id"] = tenant_id

    monkeypatch.setattr(oidc_api.oidc_service, "build_authorization_url", _mock_build_authorization_url)
    monkeypatch.setattr(oidc_api, "_store_oidc_state", _capture_store_oidc_state)

    with build_test_client(db_session=fake_db) as client:
        response = client.get("/api/v1/auth/oidc/login", params={"tenant_slug": "acme"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["authorization_url"].startswith("https://idp.example.com/authorize")
    assert captured["stored_state"] == captured["auth_state"]
    assert captured["tenant_id"] == str(tenant_id)


def test_oidc_callback_consumes_state_and_returns_tokens(monkeypatch):
    from app.api import oidc as oidc_api

    tenant_id = uuid.uuid4()
    monkeypatch.setattr(oidc_api.settings, "OIDC_SECRET_ENCRYPTION_KEY", "oidc-seed")
    provider = _make_provider(
        tenant_id=tenant_id,
        client_secret_enc=oidc_api._encrypt_secret("super-secret"),
    )
    fake_db = _FakeDBSession(providers=[provider])
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="oidc-user@example.com",
        hashed_password="not-used",
        role=UserRole.DISPATCHER,
        is_active=True,
        email_verified=True,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )

    async def _mock_consume_oidc_state(_state: str):
        return tenant_id

    async def _mock_exchange_code_for_id_token(_config, _code: str):
        return {"email": "oidc-user@example.com"}

    async def _mock_get_or_provision_user(_db, *, tenant_id, claims, config):
        assert tenant_id == provider.tenant_id
        assert claims["email"] == "oidc-user@example.com"
        assert config.client_secret == "super-secret"
        return user

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(oidc_api, "_consume_oidc_state", _mock_consume_oidc_state)
    monkeypatch.setattr(oidc_api.oidc_service, "exchange_code_for_id_token", _mock_exchange_code_for_id_token)
    monkeypatch.setattr(oidc_api.oidc_service, "get_or_provision_user", _mock_get_or_provision_user)
    monkeypatch.setattr(
        oidc_api.oidc_service,
        "build_token_response_for_user",
        lambda _user: {"access_token": "access", "refresh_token": "refresh", "token_type": "bearer"},
    )
    monkeypatch.setattr(oidc_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=fake_db) as client:
        response = client.get("/api/v1/auth/oidc/callback", params={"code": "abc123", "state": "state-123"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "access_token": "access",
        "refresh_token": "refresh",
        "token_type": "bearer",
    }
