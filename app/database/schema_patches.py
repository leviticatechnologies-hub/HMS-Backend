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


def ensure_patient_profiles_opd_schema(sync_dsn: str) -> None:
    """
    Ensure patient_profiles has OPD registration columns and a hospital+patient_id index.

    Mirrors alembic `patient_profile_opd_fields_001` for deploys where migrations lag or
    DB_BOOTSTRAP_FROM_MODELS left older column sets.
    """
    dsn = (sync_dsn or "").strip()
    if not dsn:
        logger.warning("ensure_patient_profiles_opd_schema: empty DSN, skipping")
        return

    eng = create_engine(dsn)
    try:
        insp = inspect(eng)
        if not insp.has_table("patient_profiles"):
            logger.debug("patient_profiles missing; skipping OPD schema patch")
            return

        col_names = {c["name"] for c in insp.get_columns("patient_profiles")}
        alters: list[str] = []
        if "id_type" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN id_type VARCHAR(50)")
        if "id_number" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN id_number VARCHAR(100)")
        if "id_name" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN id_name VARCHAR(255)")
        if "district" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN district VARCHAR(100)")
        if "medical_history" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN medical_history TEXT")
        if "blood_group_value" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN blood_group_value VARCHAR(50)")

        with eng.begin() as conn:
            for stmt in alters:
                logger.info("Applying patch: %s", stmt[:80])
                conn.execute(text(stmt))

            row = conn.execute(
                text(
                    """
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'patient_profiles'
                      AND column_name = 'blood_group'
                    """
                )
            ).fetchone()
            if row and row[0] is not None and row[0] < 20:
                logger.info("Applying patch: widen patient_profiles.blood_group to VARCHAR(20)")
                conn.execute(
                    text(
                        "ALTER TABLE patient_profiles "
                        "ALTER COLUMN blood_group TYPE VARCHAR(20)"
                    )
                )

            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_patient_profiles_hospital_patient_id "
                    "ON patient_profiles (hospital_id, patient_id)"
                )
            )
    finally:
        eng.dispose()


def ensure_core_schema_drift_fixes_for_database(sync_dsn: str) -> None:
    """
    Apply all idempotent column patches for a single Postgres database (platform or tenant).

    Order: patient_profiles (OPD) first, then doctor_profiles (consultation fields).
    Safe to call on every process startup and lazily on first connection.
    """
    ensure_patient_profiles_opd_schema(sync_dsn)
    ensure_doctor_profiles_consultation_schema(sync_dsn)


def ensure_doctor_profiles_consultation_schema(sync_dsn: str) -> None:
    """
    Ensure doctor_profiles has consultation_type + availability_time.

    Matches alembic `doctor_profile_consultation_fields_001` for deploys where migrations
    lag behind the SQLAlchemy model (avoids UndefinedColumnError on staff endpoints).
    """
    dsn = (sync_dsn or "").strip()
    if not dsn:
        logger.warning("ensure_doctor_profiles_consultation_schema: empty DSN, skipping")
        return

    eng = create_engine(dsn)
    try:
        insp = inspect(eng)
        if not insp.has_table("doctor_profiles"):
            logger.debug("doctor_profiles missing; skipping consultation columns patch")
            return

        col_names = {c["name"] for c in insp.get_columns("doctor_profiles")}
        alters: list[str] = []
        if "consultation_type" not in col_names:
            alters.append(
                "ALTER TABLE doctor_profiles ADD COLUMN consultation_type VARCHAR(100)"
            )
        if "availability_time" not in col_names:
            alters.append(
                "ALTER TABLE doctor_profiles ADD COLUMN availability_time TEXT"
            )

        if not alters:
            return

        logger.info(
            "Applying doctor_profiles consultation/availability column patch (%d statement(s))",
            len(alters),
        )
        with eng.begin() as conn:
            for stmt in alters:
                conn.execute(text(stmt))
    finally:
        eng.dispose()
