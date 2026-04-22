"""
Database session management for async SQLAlchemy.
- Platform DB: registry (hospitals, users, subscriptions, super admin).
- Tenant DB: one PostgreSQL database per hospital (tenant_database_name); hospital-scoped routes use it when configured.

Authentication always loads User from the platform database (see get_platform_db_session).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import AsyncGenerator, Dict, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import Request

from app.core.config import settings
from app.database.base import Base  # Re-export Base for convenience

logger = logging.getLogger(__name__)

_async_engine: Optional[AsyncEngine] = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None

_tenant_engines: Dict[str, AsyncEngine] = {}
_tenant_session_factories: Dict[str, async_sessionmaker[AsyncSession]] = {}
_tenant_lock = threading.Lock()

_hospital_tenant_cache: Dict[str, Tuple[Optional[str], float]] = {}
_CACHE_TTL_SEC = 60.0
_tenant_lab_schema_cache: Dict[str, Tuple[bool, float]] = {}

# Startup patches in main.py target DATABASE_URL_SYNC only. Tenant DBs (and workers that skip
# setup due to the advisory lock) need the same idempotent DDL applied lazily per DSN.
_schema_drift_applied: set[str] = set()
_schema_drift_lock = threading.Lock()


async def _ensure_schema_drift_for_sync_dsn(sync_dsn: str) -> None:
    """Run patient + doctor column patches once per process per database URL (idempotent)."""
    dsn = (sync_dsn or "").strip()
    if not dsn:
        return
    with _schema_drift_lock:
        if dsn in _schema_drift_applied:
            return
    from app.database.schema_patches import ensure_core_schema_drift_fixes_for_database

    await asyncio.to_thread(ensure_core_schema_drift_fixes_for_database, dsn)
    with _schema_drift_lock:
        _schema_drift_applied.add(dsn)


def get_async_engine() -> AsyncEngine:
    """
    Lazily create async engine (platform database).
    Avoids DB initialization side effects during module import on Render.
    """
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
            future=True,
        )
    return _async_engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory for the platform database."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _async_session_factory


def AsyncSessionLocal() -> AsyncSession:
    return get_session_factory()()


def get_tenant_session_factory(db_name: str) -> async_sessionmaker[AsyncSession]:
    """Async session factory for a hospital-specific database (same cluster as platform)."""
    if not db_name or not str(db_name).strip():
        raise ValueError("tenant database name is required")
    db_name = str(db_name).strip()
    with _tenant_lock:
        if db_name not in _tenant_session_factories:
            from app.services.tenant_database_provisioning import async_url_for_tenant_database

            url = async_url_for_tenant_database(db_name)
            eng = create_async_engine(
                url,
                echo=settings.DEBUG,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=3600,
                future=True,
            )
            _tenant_engines[db_name] = eng
            _tenant_session_factories[db_name] = async_sessionmaker(
                bind=eng,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
                autocommit=False,
            )
        return _tenant_session_factories[db_name]


def invalidate_hospital_tenant_cache(hospital_id: uuid.UUID) -> None:
    _hospital_tenant_cache.pop(str(hospital_id), None)


async def resolve_tenant_database_name_for_hospital(hospital_id: uuid.UUID) -> Optional[str]:
    """Read hospitals.tenant_database_name from the platform DB (cached)."""
    key = str(hospital_id)
    now = time.monotonic()
    cached = _hospital_tenant_cache.get(key)
    if cached is not None:
        name, ts = cached
        if now - ts < _CACHE_TTL_SEC:
            return name

    from app.models.tenant import Hospital

    async with get_session_factory()() as session:
        r = await session.execute(select(Hospital.tenant_database_name).where(Hospital.id == hospital_id))
        name = r.scalar_one_or_none()
    _hospital_tenant_cache[key] = (name, now)
    return name


def _use_platform_for_path(path: str) -> bool:
    """Routes that must use the platform database only."""
    if path.startswith("/api/v1/super-admin"):
        return True
    if path.startswith("/api/v1/analytics"):
        return True
    return False


async def _tenant_has_core_lab_tables(db_name: str) -> bool:
    """
    Best-effort guard for legacy tenant DBs that were provisioned before lab migrations.
    Returns True only when core lab tables exist in that tenant DB.
    """
    key = str(db_name or "").strip()
    if not key:
        return False
    now = time.monotonic()
    cached = _tenant_lab_schema_cache.get(key)
    if cached is not None:
        ok, ts = cached
        if now - ts < _CACHE_TTL_SEC:
            return ok

    fac = get_tenant_session_factory(key)
    ok = False
    async with fac() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    to_regclass('public.lab_equipment') IS NOT NULL
                    AND to_regclass('public.equipment_maintenance_logs') IS NOT NULL
                """
            )
        )
        ok = bool(result.scalar())
    _tenant_lab_schema_cache[key] = (ok, now)
    return ok


async def get_platform_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Platform DB session (users, hospitals registry, subscriptions)."""
    await _ensure_schema_drift_for_sync_dsn(settings.DATABASE_URL_SYNC)
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    Primary FastAPI dependency: platform or tenant DB based on request path + hospital context.
    """
    await _ensure_schema_drift_for_sync_dsn(settings.DATABASE_URL_SYNC)

    if not settings.TENANT_DB_ROUTE_QUERIES:
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        return

    path = request.url.path or ""

    if _use_platform_for_path(path):
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        return

    hospital_id = getattr(request.state, "hospital_id", None)
    if hospital_id is None:
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        return

    db_name = await resolve_tenant_database_name_for_hospital(hospital_id)
    if not db_name:
        logger.warning(
            "Hospital %s has no tenant_database_name; using platform DB for this request",
            hospital_id,
        )
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        return

    from app.services.tenant_database_provisioning import sync_url_for_tenant_database

    await _ensure_schema_drift_for_sync_dsn(sync_url_for_tenant_database(db_name))
    # Lab endpoints can fail with DB 500 on older tenant DBs where lab tables don't exist.
    # Fall back to platform DB instead of crashing.
    if path.startswith("/api/v1/lab/"):
        try:
            has_lab_schema = await _tenant_has_core_lab_tables(db_name)
        except Exception as e:
            logger.warning(
                "Failed to check lab schema for tenant DB '%s': %s; using platform DB",
                db_name,
                e,
            )
            has_lab_schema = False

        if not has_lab_schema:
            logger.warning(
                "Tenant DB '%s' missing core lab tables; routing lab request to platform DB",
                db_name,
            )
            async with AsyncSessionLocal() as session:
                try:
                    yield session
                except Exception:
                    await session.rollback()
                    raise
                finally:
                    await session.close()
            return

    fac = get_tenant_session_factory(db_name)
    async with fac() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


get_db = get_db_session


async def init_database():
    """Initialize database connection"""
    try:
        engine = get_async_engine()
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.fetchone()
        logger.info("Database connection established successfully")
    except Exception as e:
        logger.error("Failed to connect to database: %s", e)
        raise


async def close_database():
    """Close platform and tenant database connection pools."""
    global _async_engine, _async_session_factory, _tenant_engines, _tenant_session_factories
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
    _async_session_factory = None
    for eng in list(_tenant_engines.values()):
        await eng.dispose()
    _tenant_engines.clear()
    _tenant_session_factories.clear()
    logger.info("Database connections closed")
