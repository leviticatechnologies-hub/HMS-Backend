"""
Provision a dedicated PostgreSQL database per hospital on the same server (one pgAdmin/cluster).

The *platform* database (DATABASE_URL) holds registry rows in `hospitals` including
`tenant_database_name`. Each hospital gets `CREATE DATABASE` with a name like `{name_slug}_{8hex}_db`.

Optional: TENANT_TEMPLATE_DATABASE — if set, new DBs are cloned with
CREATE DATABASE ... WITH TEMPLATE (prepare a template DB once with your schema).

Application code still uses the single platform URL by default; routing requests to
per-tenant DBs is a separate step (pool/session per tenant_database_name).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings

logger = logging.getLogger(__name__)

# PostgreSQL non-quoted identifier: start with letter/underscore, then alphanumeric/underscore; max 63
_SAFE_DB_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def tenant_provision_http_detail(exc: BaseException, *, reactivate: bool = False) -> dict:
    """Structured API error with Postgres message and a short local-dev hint."""
    cause = (str(exc) or type(exc).__name__).strip()
    if len(cause) > 900:
        cause = cause[:900] + "…"

    low = cause.lower()
    hint: Optional[str] = None
    if any(
        m in low
        for m in (
            "permission denied",
            "insufficient_privilege",
            "must be superuser",
            "only superusers",
        )
    ):
        hint = (
            "Your Postgres user needs permission to CREATE DATABASE, or set TENANT_DB_AUTO_PROVISION=false "
            "to use a single database for all hospitals."
        )
    elif "does not exist" in low and "database" in low:
        hint = (
            "Check TENANT_DB_ADMIN_DATABASE (default postgres) and that DATABASE_URL_SYNC points at your "
            "local instance."
        )

    msg = (
        "Hospital reactivated but no new tenant database could be created."
        if reactivate
        else "Could not create dedicated database for this hospital."
    )
    out: dict = {
        "code": "TENANT_DB_PROVISION_FAILED",
        "message": msg,
        "postgres_error": cause,
    }
    if hint:
        out["hint"] = hint
    return out


def _slugify_hospital_name_for_db(name: str, max_len: int = 48) -> str:
    """Lowercase slug safe for PostgreSQL identifiers: letters, digits, underscores only."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "hospital"
    if s[0].isdigit():
        s = "h_" + s
    return s[:max_len].rstrip("_")


def tenant_db_name_for_hospital(hospital_id, hospital_name: Optional[str] = None) -> str:
    """
    Unique per-hospital database name on the same Postgres instance.

    With a display name: ``{slug}_{first_8_hex_of_uuid}_db`` (e.g. nagendra_uggirala_0455325a_db).
    Without name: legacy ``hosp_`` + 32-char hex (prefix from TENANT_DB_NAME_PREFIX).
    """
    hid = str(hospital_id).replace("-", "")
    short = hid[:8]

    if (hospital_name or "").strip():
        slug = _slugify_hospital_name_for_db(hospital_name or "")
        # 63-char PostgreSQL limit: trim slug if needed; keep _{8}_db suffix
        suffix = f"_{short}_db"
        max_slug = 63 - len(suffix)
        if max_slug < 8:
            name = f"hosp_{hid}"[:63]
        else:
            base = slug[:max_slug].rstrip("_")
            name = f"{base}{suffix}"
            if len(name) > 63:
                name = name[:63]
    else:
        prefix = (settings.TENANT_DB_NAME_PREFIX or "hosp_").strip().lower()
        if not prefix.endswith("_"):
            prefix = prefix + "_"
        name = f"{prefix}{hid}"

    if not _SAFE_DB_NAME.match(name):
        name = f"hosp_{hid}"[:63]
    return name


def _admin_sync_url() -> str:
    """Sync URL connected to the maintenance DB (default postgres) to run CREATE DATABASE."""
    sync = (settings.DATABASE_URL_SYNC or "").strip()
    if not sync:
        raise RuntimeError("DATABASE_URL_SYNC is required for tenant DB provisioning")
    u = make_url(sync)
    maint = (settings.TENANT_DB_ADMIN_DATABASE or "postgres").strip()
    u = u.set(database=maint)
    return u.render_as_string(hide_password=False)


def provision_postgres_database(
    db_name: str,
    template_database: Optional[str] = None,
) -> None:
    """
    Create a new database on the same PostgreSQL instance as DATABASE_URL_SYNC.
    Caller must ensure db_name is safe (use tenant_db_name_for_hospital).
    """
    if not _SAFE_DB_NAME.match(db_name):
        raise ValueError(f"Unsafe database name: {db_name!r}")

    admin_url = _admin_sync_url()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    tpl = (template_database or settings.TENANT_TEMPLATE_DATABASE or "").strip()
    if tpl and not _SAFE_DB_NAME.match(tpl):
        raise ValueError(f"Unsafe template database name: {tpl!r}")

    with engine.connect() as conn:
        # Does target already exist?
        row = conn.execute(
            text(
                "SELECT 1 FROM pg_database WHERE datname = :name"
            ),
            {"name": db_name},
        ).scalar()
        if row:
            logger.info("Tenant database already exists: %s", db_name)
            return

        # Omit OWNER — defaults to connecting role. "OWNER CURRENT_USER" breaks on some hosts/parsers.
        if tpl:
            stmt = text(
                f'CREATE DATABASE "{db_name}" WITH TEMPLATE "{tpl}"'
            )
            logger.info("Creating tenant database %s FROM TEMPLATE %s", db_name, tpl)
        else:
            stmt = text(
                f'CREATE DATABASE "{db_name}" ENCODING \'UTF8\''
            )
            logger.info("Creating empty tenant database %s (no template)", db_name)

        conn.execute(stmt)


def sync_url_for_tenant_database(db_name: str) -> str:
    """Build sync DSN for a tenant database (same host/user as platform)."""
    if not _SAFE_DB_NAME.match(db_name):
        raise ValueError(f"Unsafe database name: {db_name!r}")
    sync = (settings.DATABASE_URL_SYNC or "").strip()
    u = make_url(sync)
    u = u.set(database=db_name)
    return u.render_as_string(hide_password=False)


def async_url_for_tenant_database(db_name: str) -> str:
    """Build async DSN (+asyncpg) for a tenant database."""
    s = sync_url_for_tenant_database(db_name)
    u = make_url(s)
    driver = (u.drivername or "").lower()
    if driver == "postgresql+asyncpg":
        return u.render_as_string(hide_password=False)
    if driver.startswith("postgresql"):
        return u.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    if driver == "postgres":
        return u.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    return s


def ensure_tenant_schema(db_name: str) -> None:
    """
    Create tables from SQLAlchemy models in an empty tenant database.
    Skipped when TENANT_TEMPLATE_DATABASE is used (clone already has schema).
    """
    import app.models  # noqa: F401 — register all models on Base.metadata

    from app.database.base import Base

    url = sync_url_for_tenant_database(db_name)
    eng = create_engine(url)
    try:
        Base.metadata.create_all(bind=eng)
    finally:
        eng.dispose()
    from app.database.schema_patches import (
        ensure_core_schema_drift_fixes_for_database,
        ensure_hospitals_tenant_database_name_column,
    )

    ensure_hospitals_tenant_database_name_column(url)
    ensure_core_schema_drift_fixes_for_database(url)


def copy_hospital_registry_row_to_tenant(db_name: str, hospital: Any) -> None:
    """Mirror the platform hospital registry row into the tenant DB (for HospitalAdmin lookups)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.tenant import Hospital as HospModel

    url = sync_url_for_tenant_database(db_name)
    eng = create_engine(url)
    Session = sessionmaker(bind=eng)
    try:
        with Session() as s:
            if s.get(HospModel, hospital.id):
                return
            row = HospModel(
                id=hospital.id,
                name=hospital.name,
                registration_number=hospital.registration_number,
                email=hospital.email,
                phone=hospital.phone,
                address=hospital.address,
                city=hospital.city,
                state=hospital.state,
                country=hospital.country,
                pincode=hospital.pincode,
                license_number=getattr(hospital, "license_number", None),
                established_date=getattr(hospital, "established_date", None),
                website=getattr(hospital, "website", None),
                logo_url=getattr(hospital, "logo_url", None),
                is_active=hospital.is_active,
                status=hospital.status,
                settings=hospital.settings or {},
                tenant_database_name=getattr(hospital, "tenant_database_name", None),
            )
            s.add(row)
            s.commit()
    finally:
        eng.dispose()


def bootstrap_tenant_database(db_name: str, hospital: Any, created_from_template: bool) -> None:
    """
    After CREATE DATABASE: apply schema (if empty DB) and insert this hospital row in the tenant DB.
    """
    if not created_from_template:
        ensure_tenant_schema(db_name)
    copy_hospital_registry_row_to_tenant(db_name, hospital)
