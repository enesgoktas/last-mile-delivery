import uuid
from datetime import datetime
from types import SimpleNamespace

from fastapi import HTTPException, status

from app.config import settings
from app.models.enums import UserRole
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.auth import create_access_token, hash_password
from app.utils.compat import UTC
from tests.integration.test_utils import build_test_client, find_route_dependency

STRONG_PASSWORD = "Password1!"


class _ScalarValues:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)

    def unique(self):
        return self

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None


class _ScalarResult:
    def __init__(self, values):
        if isinstance(values, list):
            self._values = values
        elif values is None:
            self._values = []
        else:
            self._values = [values]

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return _ScalarValues(self._values)


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
    def __init__(self, *, users: list[User] | None = None, tenants: list[Tenant] | None = None):
        self.users_by_email: dict[str, list[User]] = {}
        self.users_by_id: dict[uuid.UUID, User] = {}
        self.tenants_by_slug: dict[str, Tenant] = {}
        self.tenants_by_id: dict[uuid.UUID, Tenant] = {}
        for tenant in tenants or []:
            self._store_tenant(tenant)
        for user in users or []:
            self._store_user(user)

    def _store_tenant(self, tenant: Tenant) -> None:
        if tenant.id is None:
            tenant.id = uuid.uuid4()
        if tenant.created_at is None:
            tenant.created_at = datetime.now(UTC).replace(tzinfo=None)
        self.tenants_by_slug[tenant.slug] = tenant
        self.tenants_by_id[tenant.id] = tenant

    def _store_user(self, user: User) -> None:
        if user.id is None:
            user.id = uuid.uuid4()
        if user.created_at is None:
            user.created_at = datetime.now(UTC).replace(tzinfo=None)
        if user.is_active is None:
            user.is_active = True
        self.users_by_email.setdefault(user.email, []).append(user)
        self.users_by_id[user.id] = user

    async def execute(self, query):
        slug = _extract_filter_value(query, "slug")
        if slug is not None:
            return _ScalarResult(self.tenants_by_slug.get(slug))

        email = _extract_filter_value(query, "email")
        tenant_id = _extract_filter_value(query, "tenant_id")
        if email is not None:
            users = list(self.users_by_email.get(email, []))
            if tenant_id is not None:
                users = [user for user in users if user.tenant_id == tenant_id]
            return _ScalarResult(users)

        user_id = _extract_filter_value(query, "id")
        if user_id is not None:
            user = self.users_by_id.get(user_id)
            if user is not None and tenant_id is not None and user.tenant_id != tenant_id:
                user = None
            if user is not None:
                return _ScalarResult(user)
            tenant = self.tenants_by_id.get(user_id)
            return _ScalarResult(tenant)

        if tenant_id is not None:
            return _ScalarResult(
                [user for user in self.users_by_id.values() if user.tenant_id == tenant_id]
            )

        return _ScalarResult(None)

    def add(self, obj):
        if isinstance(obj, User):
            self._store_user(obj)
        if isinstance(obj, Tenant):
            self._store_tenant(obj)

    async def flush(self):
        return None

    async def refresh(self, _obj):
        return None


def _make_user(
    *,
    email: str,
    tenant_id: uuid.UUID | None = None,
    role: UserRole = UserRole.DISPATCHER,
    password: str = STRONG_PASSWORD,
    is_active: bool = True,
) -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        email=email,
        hashed_password=hash_password(password),
        role=role,
        is_active=is_active,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )


def _make_tenant(*, name: str, slug: str) -> Tenant:
    return Tenant(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        is_active=True,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )


def test_register_user_success(monkeypatch):
    from app.api import auth as auth_api

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)
    fake_db = _FakeDBSession()
    role_dep = find_route_dependency("/api/v1/auth/register", "_admin")
    tenant_id = uuid.uuid4()

    async def _override_admin():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role=UserRole.ADMIN, is_active=True)

    with build_test_client(db_session=fake_db, dependency_overrides={role_dep: _override_admin}) as client:
        response = client.post(
            "/api/v1/auth/register",
            headers={"Authorization": "Bearer test-access-token"},
            json={
                "email": "courier@example.com",
                "password": STRONG_PASSWORD,
                "role": "COURIER",
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body["email"] == "courier@example.com"
    assert body["role"] == "COURIER"
    assert body["is_active"] is True
    assert body["tenant_id"] == str(tenant_id)
    assert fake_db.users_by_email["courier@example.com"][0].tenant_id == tenant_id


def test_register_user_conflict_when_email_exists(monkeypatch):
    from app.api import auth as auth_api

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)
    tenant_id = uuid.uuid4()
    fake_db = _FakeDBSession(users=[_make_user(email="existing@example.com", tenant_id=tenant_id)])
    role_dep = find_route_dependency("/api/v1/auth/register", "_admin")

    async def _override_admin():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role=UserRole.ADMIN, is_active=True)

    with build_test_client(db_session=fake_db, dependency_overrides={role_dep: _override_admin}) as client:
        response = client.post(
            "/api/v1/auth/register",
            headers={"Authorization": "Bearer test-access-token"},
            json={
                "email": "existing@example.com",
                "password": STRONG_PASSWORD,
                "role": "DISPATCHER",
            },
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Email already registered"


def test_register_user_forbidden_when_role_dependency_rejects():
    role_dep = find_route_dependency("/api/v1/auth/register", "_admin")

    async def _forbidden_admin():
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    with build_test_client(dependency_overrides={role_dep: _forbidden_admin}) as client:
        response = client.post(
            "/api/v1/auth/register",
            headers={"Authorization": "Bearer test-access-token"},
            json={
                "email": "blocked@example.com",
                "password": STRONG_PASSWORD,
                "role": "COURIER",
            },
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Insufficient permissions"


def test_login_success_returns_token_pair(monkeypatch):
    from app.api import auth as auth_api

    user = _make_user(
        email="dispatcher@example.com",
        role=UserRole.DISPATCHER,
        password="secret123",
    )
    fake_db = _FakeDBSession(users=[user])

    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _noop_rate_limit)
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "dispatcher@example.com", "password": "secret123"},
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


def test_login_requires_tenant_slug_when_email_exists_in_multiple_workspaces(monkeypatch):
    from app.api import auth as auth_api

    tenant_one = _make_tenant(name="Acme", slug="acme")
    tenant_two = _make_tenant(name="Globex", slug="globex")
    shared_email = "shared@example.com"
    fake_db = _FakeDBSession(
        tenants=[tenant_one, tenant_two],
        users=[
            _make_user(email=shared_email, tenant_id=tenant_one.id, password="secret123"),
            _make_user(email=shared_email, tenant_id=tenant_two.id, password="secret123"),
        ],
    )

    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _noop_rate_limit)
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": shared_email, "password": "secret123"},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "tenant_slug" in response.json()["detail"]


def test_login_uses_tenant_slug_to_select_workspace(monkeypatch):
    from app.api import auth as auth_api

    tenant_one = _make_tenant(name="Acme", slug="acme")
    tenant_two = _make_tenant(name="Globex", slug="globex")
    shared_email = "shared@example.com"
    user_one = _make_user(email=shared_email, tenant_id=tenant_one.id, password="secret123")
    user_two = _make_user(email=shared_email, tenant_id=tenant_two.id, password="secret123")
    fake_db = _FakeDBSession(tenants=[tenant_one, tenant_two], users=[user_one, user_two])

    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _noop_rate_limit)
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": shared_email,
                "password": "secret123",
                "tenant_slug": "globex",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["access_token"]


def test_login_uses_configured_rate_limit(monkeypatch):
    from app.api import auth as auth_api

    user = _make_user(
        email="dispatcher-rate@example.com",
        role=UserRole.DISPATCHER,
        password="secret123",
    )
    fake_db = _FakeDBSession(users=[user])
    captured: dict[str, object] = {}

    async def _capture_rate_limit(**kwargs):
        captured.update(kwargs)

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _capture_rate_limit)
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "dispatcher-rate@example.com", "password": "secret123"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert captured["limit"] == settings.AUTH_RATE_LIMIT_PER_MIN
    assert captured["window_seconds"] == 60
    assert str(captured["key"]).startswith("rl:auth:login:")


def test_login_rate_limited_returns_429(monkeypatch):
    from app.api import auth as auth_api

    async def _rate_limited(*_args, **_kwargs):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _rate_limited)

    with build_test_client(db_session=_FakeDBSession()) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "wrongpass"},
        )

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_refresh_rotates_tokens_and_revokes_previous(monkeypatch):
    from app.api import auth as auth_api

    user = _make_user(email="admin@example.com", role=UserRole.ADMIN)
    revoked_tokens: list[str] = []

    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _mock_get_user_from_refresh_token(_token, _db):
        return user

    async def _mock_revoke(token: str):
        revoked_tokens.append(token)

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _noop_rate_limit)
    monkeypatch.setattr(auth_api, "get_user_from_refresh_token", _mock_get_user_from_refresh_token)
    monkeypatch.setattr(auth_api, "revoke_token", _mock_revoke)
    monkeypatch.setattr(auth_api, "create_access_token", lambda *_args, **_kwargs: "new_access_token")
    monkeypatch.setattr(auth_api, "create_refresh_token", lambda *_args, **_kwargs: "new_refresh_token")
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=_FakeDBSession()) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "old_refresh_token"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["access_token"] == "new_access_token"
    assert response.json()["refresh_token"] == "new_refresh_token"
    assert revoked_tokens == ["old_refresh_token"]


def test_refresh_uses_configured_rate_limit(monkeypatch):
    from app.api import auth as auth_api

    user = _make_user(email="admin-rate@example.com", role=UserRole.ADMIN)
    captured: dict[str, object] = {}

    async def _capture_rate_limit(**kwargs):
        captured.update(kwargs)

    async def _mock_get_user_from_refresh_token(_token, _db):
        return user

    async def _mock_revoke(_token: str):
        return None

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _capture_rate_limit)
    monkeypatch.setattr(auth_api, "get_user_from_refresh_token", _mock_get_user_from_refresh_token)
    monkeypatch.setattr(auth_api, "revoke_token", _mock_revoke)
    monkeypatch.setattr(auth_api, "create_access_token", lambda *_args, **_kwargs: "new_access_token")
    monkeypatch.setattr(auth_api, "create_refresh_token", lambda *_args, **_kwargs: "new_refresh_token")
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=_FakeDBSession()) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "old_refresh_token"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert captured["limit"] == settings.AUTH_RATE_LIMIT_PER_MIN
    assert captured["window_seconds"] == 60
    assert str(captured["key"]).startswith("rl:auth:refresh:")


def test_me_endpoint_returns_current_user():
    me_dep = find_route_dependency("/api/v1/auth/me", "current_user")
    user_id = uuid.uuid4()

    async def _override_user():
        return SimpleNamespace(
            id=user_id,
            tenant_id=uuid.uuid4(),
            email="courier@example.com",
            role=UserRole.COURIER,
            is_active=True,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )

    with build_test_client(dependency_overrides={me_dep: _override_user}) as client:
        response = client.get("/api/v1/auth/me")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["email"] == "courier@example.com"
    assert body["role"] == "COURIER"
    assert body["tenant_id"]


def test_me_endpoint_rejects_revoked_access_token(monkeypatch):
    import app.utils.auth as auth_utils

    user = _make_user(email="revoked@example.com", role=UserRole.COURIER)
    fake_db = _FakeDBSession(users=[user])
    token = create_access_token(str(user.id), user.role.value, str(user.tenant_id))

    async def _revoked(_jti):
        return True

    monkeypatch.setattr(auth_utils, "is_token_revoked", _revoked)

    with build_test_client(db_session=fake_db) as client:
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Access token revoked"


def test_me_endpoint_rejects_inactive_user(monkeypatch):
    import app.utils.auth as auth_utils

    user = _make_user(
        email="inactive@example.com",
        tenant_id=uuid.uuid4(),
        role=UserRole.DISPATCHER,
        is_active=False,
    )
    fake_db = _FakeDBSession(users=[user])
    token = create_access_token(str(user.id), user.role.value, str(user.tenant_id))

    async def _not_revoked(_jti):
        return False

    monkeypatch.setattr(auth_utils, "is_token_revoked", _not_revoked)

    with build_test_client(db_session=fake_db) as client:
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "User not found or inactive"


def test_logout_revokes_access_and_optional_refresh(monkeypatch):
    from app.api import auth as auth_api

    logout_dep = find_route_dependency("/api/v1/auth/logout", "current_user")
    revoked_tokens: list[str] = []

    async def _override_user():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=UserRole.DISPATCHER, is_active=True)

    async def _mock_revoke(token: str):
        revoked_tokens.append(token)

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "revoke_token", _mock_revoke)
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)

    with build_test_client(dependency_overrides={logout_dep: _override_user}) as client:
        response = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "refresh_123"},
            headers={"Authorization": "Bearer access_456"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "ok"
    assert revoked_tokens == ["access_456", "refresh_123"]


def test_signup_creates_tenant_and_owner(monkeypatch):
    from app.api import auth as auth_api

    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _noop_rate_limit)
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)
    fake_db = _FakeDBSession()

    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/auth/signup",
            json={
                "company_name": "Acme Logistics",
                "full_name": "Jordan Lee",
                "email": "owner@acme-logistics.com",
                "password": STRONG_PASSWORD,
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["access_token"]
    tenant = fake_db.tenants_by_slug["acme-logistics"]
    owner = fake_db.users_by_email["owner@acme-logistics.com"][0]
    assert tenant.name == "Acme Logistics"
    assert owner.role == UserRole.OWNER
    assert owner.tenant_id == tenant.id


def test_signup_uses_configured_rate_limit(monkeypatch):
    from app.api import auth as auth_api

    captured: dict[str, object] = {}

    async def _capture_rate_limit(**kwargs):
        captured.update(kwargs)

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _capture_rate_limit)
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)
    fake_db = _FakeDBSession()

    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/auth/signup",
            json={
                "company_name": "Acme Logistics",
                "full_name": "Jordan Lee",
                "email": "owner@acme-logistics.com",
                "password": STRONG_PASSWORD,
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert captured["limit"] == settings.AUTH_SIGNUP_RATE_LIMIT_PER_MIN
    assert captured["window_seconds"] == 60
    assert str(captured["key"]).startswith("rl:auth:signup:")


def test_signup_allows_same_email_in_a_new_workspace(monkeypatch):
    from app.api import auth as auth_api

    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _noop_audit(*_args, **_kwargs):
        return None

    existing_tenant = _make_tenant(name="Acme Logistics", slug="acme-logistics")
    existing_owner = _make_user(
        email="owner@example.com",
        tenant_id=existing_tenant.id,
        role=UserRole.OWNER,
    )
    fake_db = _FakeDBSession(users=[existing_owner], tenants=[existing_tenant])

    monkeypatch.setattr(auth_api, "enforce_fixed_window_limit", _noop_rate_limit)
    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)

    with build_test_client(db_session=fake_db) as client:
        response = client.post(
            "/api/v1/auth/signup",
            json={
                "company_name": "Globex",
                "full_name": "Jordan Lee",
                "email": "owner@example.com",
                "password": STRONG_PASSWORD,
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["access_token"]
    assert len(fake_db.users_by_email["owner@example.com"]) == 2


def test_register_inherits_tenant(monkeypatch):
    from app.api import auth as auth_api

    async def _noop_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "log_audit_event", _noop_audit)
    fake_db = _FakeDBSession()
    role_dep = find_route_dependency("/api/v1/auth/register", "_admin")
    tenant_id = uuid.uuid4()

    async def _override_admin():
        return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role=UserRole.ADMIN, is_active=True)

    with build_test_client(db_session=fake_db, dependency_overrides={role_dep: _override_admin}) as client:
        response = client.post(
            "/api/v1/auth/register",
            headers={"Authorization": "Bearer test-access-token"},
            json={
                "email": "dispatcher@example.com",
                "password": STRONG_PASSWORD,
                "role": "DISPATCHER",
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["tenant_id"] == str(tenant_id)
    assert fake_db.users_by_email["dispatcher@example.com"][0].tenant_id == tenant_id
