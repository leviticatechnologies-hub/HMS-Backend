"""OPD management: visits, consultations, vitals, token log, transfers.

Revision ID: opd_management_001
Revises: appointments_widen_appointment_time_002

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "opd_management_001"
down_revision = "appointments_widen_appointment_time_002"
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    from sqlalchemy import inspect

    return name in inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()
    u = postgresql.UUID(as_uuid=True)

    if not _table_exists(conn, "opd_visits"):
        op.create_table(
            "opd_visits",
            sa.Column("id", u, primary_key=True),
            sa.Column("hospital_id", u, sa.ForeignKey("hospitals.id"), nullable=False, index=True),
            sa.Column("opd_ref", sa.String(40), nullable=False),
            sa.Column("patient_profile_id", u, sa.ForeignKey("patient_profiles.id"), nullable=False, index=True),
            sa.Column("patient_name", sa.String(255), nullable=False),
            sa.Column("age", sa.Integer(), nullable=True),
            sa.Column("gender", sa.String(20), nullable=True),
            sa.Column("phone_no", sa.String(30), nullable=True),
            sa.Column("blood_group", sa.String(20), nullable=True),
            sa.Column("token_no", sa.String(40), nullable=False),
            sa.Column("visit_type", sa.String(30), nullable=False, server_default="NEW"),
            sa.Column("priority", sa.String(20), nullable=False, server_default="NORMAL"),
            sa.Column("department_name", sa.String(200), nullable=True),
            sa.Column("department_id", u, sa.ForeignKey("departments.id"), nullable=True),
            sa.Column("doctor_user_id", u, sa.ForeignKey("users.id"), nullable=True, index=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="WAITING", index=True),
            sa.Column("queue_position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("waiting_time", sa.Integer(), nullable=True),
            sa.Column("arrival_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("appointment_id", u, sa.ForeignKey("appointments.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.UniqueConstraint("hospital_id", "opd_ref", name="uq_opd_visits_hospital_opd_ref"),
        )

    if not _table_exists(conn, "opd_consultations"):
        op.create_table(
            "opd_consultations",
            sa.Column("id", u, primary_key=True),
            sa.Column("hospital_id", u, sa.ForeignKey("hospitals.id"), nullable=False, index=True),
            sa.Column("opd_visit_id", u, sa.ForeignKey("opd_visits.id"), nullable=False, unique=True),
            sa.Column("patient_profile_id", u, sa.ForeignKey("patient_profiles.id"), nullable=False),
            sa.Column("doctor_user_id", u, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("consultation_type", sa.String(30), nullable=False, server_default="NEW"),
            sa.Column("symptoms", sa.Text(), nullable=True),
            sa.Column("diagnosis", sa.Text(), nullable=True),
            sa.Column("prescription", sa.Text(), nullable=True),
            sa.Column("tests_recommended", sa.JSON(), nullable=True),
            sa.Column("remarks", sa.Text(), nullable=True),
            sa.Column("next_visit_date", sa.Date(), nullable=True),
            sa.Column("medical_record_id", u, sa.ForeignKey("medical_records.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        )

    if not _table_exists(conn, "opd_vital_signs"):
        op.create_table(
            "opd_vital_signs",
            sa.Column("id", u, primary_key=True),
            sa.Column("hospital_id", u, sa.ForeignKey("hospitals.id"), nullable=False, index=True),
            sa.Column("consultation_id", u, sa.ForeignKey("opd_consultations.id"), nullable=False, unique=True),
            sa.Column("bp", sa.String(30), nullable=True),
            sa.Column("pulse", sa.Integer(), nullable=True),
            sa.Column("temperature", sa.DECIMAL(5, 2), nullable=True),
            sa.Column("spo2", sa.Integer(), nullable=True),
            sa.Column("weight", sa.DECIMAL(8, 2), nullable=True),
            sa.Column("height", sa.DECIMAL(8, 2), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        )

    if not _table_exists(conn, "opd_token_logs"):
        op.create_table(
            "opd_token_logs",
            sa.Column("id", u, primary_key=True),
            sa.Column("hospital_id", u, sa.ForeignKey("hospitals.id"), nullable=False, index=True),
            sa.Column("token_no", sa.String(40), nullable=False, index=True),
            sa.Column("patient_profile_id", u, sa.ForeignKey("patient_profiles.id"), nullable=False),
            sa.Column("doctor_user_id", u, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("generated_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(30), nullable=False, server_default="ACTIVE"),
            sa.Column("opd_visit_id", u, sa.ForeignKey("opd_visits.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        )

    if not _table_exists(conn, "opd_patient_transfers"):
        op.create_table(
            "opd_patient_transfers",
            sa.Column("id", u, primary_key=True),
            sa.Column("hospital_id", u, sa.ForeignKey("hospitals.id"), nullable=False, index=True),
            sa.Column("opd_visit_id", u, sa.ForeignKey("opd_visits.id"), nullable=False, index=True),
            sa.Column("patient_profile_id", u, sa.ForeignKey("patient_profiles.id"), nullable=False),
            sa.Column("from_doctor_user_id", u, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("to_doctor_user_id", u, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("reason", sa.String(500), nullable=True),
            sa.Column("transferred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    for t in (
        "opd_patient_transfers",
        "opd_token_logs",
        "opd_vital_signs",
        "opd_consultations",
        "opd_visits",
    ):
        if _table_exists(conn, t):
            op.drop_table(t)
