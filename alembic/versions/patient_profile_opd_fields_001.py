"""OPD patient profile: ID, district, medical history, blood group other

Revision ID: patient_profile_opd_fields_001
Revises: hospital_tenant_database_001
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "patient_profile_opd_fields_001"
down_revision = "hospital_tenant_database_001"
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    return name in inspect(conn).get_table_names()


def _column_exists(conn, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return column in {c["name"] for c in inspect(conn).get_columns(table)}


def upgrade():
    conn = op.get_bind()
    if not _table_exists(conn, "patient_profiles"):
        return
    cols = [
        ("id_type", sa.String(length=50), True),
        ("id_number", sa.String(length=100), True),
        ("id_name", sa.String(length=255), True),
        ("district", sa.String(length=100), True),
        ("medical_history", sa.Text(), True),
        ("blood_group_value", sa.String(length=50), True),
    ]
    for name, coltype, nullable in cols:
        if not _column_exists(conn, "patient_profiles", name):
            op.add_column("patient_profiles", sa.Column(name, coltype, nullable=nullable))

    # Faster receptionist lookups by PAT-... within a hospital
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_patient_profiles_hospital_patient_id "
            "ON patient_profiles (hospital_id, patient_id)"
        )
    )


def downgrade():
    conn = op.get_bind()
    if _table_exists(conn, "patient_profiles"):
        op.execute(sa.text("DROP INDEX IF EXISTS ix_patient_profiles_hospital_patient_id"))
    if not _table_exists(conn, "patient_profiles"):
        return
    for name in (
        "blood_group_value",
        "medical_history",
        "district",
        "id_name",
        "id_number",
        "id_type",
    ):
        if _column_exists(conn, "patient_profiles", name):
            op.drop_column("patient_profiles", name)
