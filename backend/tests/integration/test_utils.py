from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.database import get_db, get_write_db
from app.main import app


def find_route_dependency(route_path: str, dep_name: str, method: str | None = None) -> Any:
    fallback_candidates: list[Any] = []
    normalized_method = method.upper() if method is not None else None
    for route in app.routes:
        if getattr(route, "path", None) != route_path:
            continue
        if normalized_method is not None and normalized_method not in getattr(route, "methods", set()):
            continue
        for dependency in route.dependant.dependencies:
            if dependency.name == dep_name:
                return dependency.call
            if dependency.name in {"_user", "_admin", "current_user"}:
                fallback_candidates.append(dependency.call)
    if dep_name in {"_user", "_admin", "current_user"}:
        unique_calls = list(dict.fromkeys(fallback_candidates))
        if len(unique_calls) == 1:
            return unique_calls[0]
    raise AssertionError(f"Dependency {dep_name} not found for route: {route_path}")


@contextmanager
def build_test_client(
    *,
    dependency_overrides: dict[Any, Any] | None = None,
    db_session: Any | None = None,
) -> TestClient:
    from app.config import settings

    original_skip_checks = settings.SKIP_DB_STARTUP_CHECKS
    settings.SKIP_DB_STARTUP_CHECKS = True

    async def _override_db():
        yield db_session if db_session is not None else SimpleNamespace()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_write_db] = _override_db
    for dependency, override in (dependency_overrides or {}).items():
        app.dependency_overrides[dependency] = override

    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        settings.SKIP_DB_STARTUP_CHECKS = original_skip_checks
