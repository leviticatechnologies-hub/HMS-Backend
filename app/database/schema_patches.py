"""
Idempotent DDL for schema drift between deployed databases and current models.

`create_all` does not add columns to existing tables; Alembic may not run if env
URLs or image revision sets differ from local. These patches keep critical
columns present without requiring manual SQL on every deploy.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text

logger = logging.getLogger(__name__)


def _sync_url_from_env_async(async_url: str) -> str:
    value = (async_url or "").strip()
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql://", 1)
    elif value.startswith("postgresql+asyncpg://"):
        value = value.replace("postgresql+asyncpg://", "postgresql://", 1)
    return value


def ensure_hospitals_tenant_database_name_column(sync_dsn: str) -> None:
    """Add hospitals.tenant_database_name + unique index if missing."""
    dsn = (sync_dsn or "").strip()
    if not dsn:
        logger.warning("ensure_hospitals_tenant_database_name_column: empty DSN, skipping")
        return

    eng = create_engine(dsn)
    try:
        insp = inspect(eng)
        if not insp.has_table("hospitals"):
            logger.debug("hospitals table missing; skipping tenant_database_name patch")
            return
        cols = {c["name"] for c in insp.get_columns("hospitals")}
        if "tenant_database_name" in cols:
            return
        logger.info("Applying patch: add hospitals.tenant_database_name (deploy / drift fix)")
        with eng.begin() as conn:
            conn.execute(
                text("ALTER TABLE hospitals ADD COLUMN tenant_database_name VARCHAR(63)")
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_hospitals_tenant_database_name "
                    "ON hospitals (tenant_database_name)"
                )
            )
    finally:
        eng.dispose()
