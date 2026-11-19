import contextvars
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, with_loader_criteria
from sqlalchemy.pool import NullPool

from app.config import settings
from app.utils.tenant_context import get_current_tenant_scope

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

celery_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)
celery_async_session = async_sessionmaker(
    celery_engine, class_=AsyncSession, expire_on_commit=False,
)

_session_factory_override: contextvars.ContextVar[async_sessionmaker | None] = (
    contextvars.ContextVar("_session_factory_override", default=None)
)


def get_async_session_factory() -> async_sessionmaker:
    """Return the active session factory.

    Inside Celery tasks, the override is set to ``celery_async_session``
    (NullPool engine) so each coroutine gets a fresh connection that isn't
    tied to a prior event loop.  FastAPI code never sets the override, so
    the default pooled factory is returned.
    """
    return _session_factory_override.get() or async_session


class Base(DeclarativeBase):
    pass


@event.listens_for(AsyncSession.sync_session_class, "do_orm_execute")
def _apply_tenant_scope(execute_state) -> None:
    if (
        not execute_state.is_select
        or execute_state.is_relationship_load
        or execute_state.is_column_load
        or execute_state.execution_options.get("skip_tenant_scope", False)
    ):
        return

    tenant_id = get_current_tenant_scope()
    if tenant_id is None:
        return

    statement = execute_state.statement
    for mapper in Base.registry.mappers:
        model = mapper.class_
        if hasattr(model, "tenant_id"):
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda cls: cls.tenant_id == tenant_id,
                    include_aliases=True,
                )
            )
    execute_state.statement = statement


async def get_db():
    """Read-only FastAPI dependency that never commits implicitly."""
    async with _managed_session(transactional=False) as session:
        yield session


async def get_write_db():
    """Transactional FastAPI dependency that commits on successful exit."""
    async with _managed_session(transactional=True) as session:
        yield session


@asynccontextmanager
async def _managed_session(*, transactional: bool) -> AsyncIterator[AsyncSession]:
    session_factory = get_async_session_factory()
    async with session_factory() as session:
        try:
            yield session
            if transactional and session.in_transaction():
                await session.commit()
        except Exception:
            if session.in_transaction():
                await session.rollback()
            raise
        finally:
            if not transactional and session.in_transaction():
                await session.rollback()
