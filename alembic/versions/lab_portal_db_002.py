"""Lab portal DB tables for UI modules.

Revision ID: lab_portal_db_002
Revises: lab_v2_minimal_001
Create Date: 2026-04-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "lab_portal_db_002"
down_revision = "lab_v2_minimal_001"
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    return name in inspect(conn).get_table_names()


def _add_common(cols):
    return cols + [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"]),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "lab_test_registrations"):
        op.create_table(
            "lab_test_registrations",
            *_add_common([
                sa.Column("test_id", sa.String(50), nullable=False),
                sa.Column("patient_ref", sa.String(80), nullable=True),
                sa.Column("patient_name", sa.String(120), nullable=False),
                sa.Column("doctor_name", sa.String(120), nullable=True),
                sa.Column("test_type", sa.String(120), nullable=False),
                sa.Column("sample_type", sa.String(40), nullable=False),
                sa.Column("priority", sa.String(20), nullable=False),
                sa.Column("status", sa.String(30), nullable=False),
                sa.Column("special_instructions", sa.Text(), nullable=True),
                sa.Column("registered_date", sa.Date(), nullable=False),
            ])
        )
        op.create_index("ix_lab_test_registrations_test_id", "lab_test_registrations", ["test_id"], unique=True)

    if not _table_exists(conn, "lab_critical_alerts"):
        op.create_table(
            "lab_critical_alerts",
            *_add_common([
                sa.Column("alert_id", sa.String(60), nullable=False),
                sa.Column("test_id", sa.String(60), nullable=False),
                sa.Column("patient_ref", sa.String(80), nullable=True),
                sa.Column("patient_name", sa.String(120), nullable=False),
                sa.Column("doctor_name", sa.String(120), nullable=True),
                sa.Column("test_name", sa.String(120), nullable=False),
                sa.Column("result_value", sa.String(80), nullable=False),
                sa.Column("alert_level", sa.String(20), nullable=False),
                sa.Column("result_time_label", sa.String(30), nullable=False),
                sa.Column("notify_status", sa.String(20), nullable=False),
                sa.Column("acknowledged", sa.String(5), nullable=False),
            ])
        )
        op.create_index("ix_lab_critical_alerts_alert_id", "lab_critical_alerts", ["alert_id"], unique=True)

    if not _table_exists(conn, "lab_sample_tracking"):
        op.create_table(
            "lab_sample_tracking",
            *_add_common([
                sa.Column("barcode", sa.String(60), nullable=False),
                sa.Column("test_id", sa.String(60), nullable=False),
                sa.Column("patient_ref", sa.String(80), nullable=True),
                sa.Column("patient_name", sa.String(120), nullable=False),
                sa.Column("doctor_name", sa.String(120), nullable=True),
                sa.Column("test_type", sa.String(120), nullable=False),
                sa.Column("sample_type", sa.String(40), nullable=False),
                sa.Column("collection_time", sa.String(40), nullable=False),
                sa.Column("status", sa.String(30), nullable=False),
                sa.Column("current_location", sa.String(160), nullable=False),
            ])
        )
        op.create_index("ix_lab_sample_tracking_barcode", "lab_sample_tracking", ["barcode"], unique=True)

    if not _table_exists(conn, "lab_report_records"):
        op.create_table(
            "lab_report_records",
            *_add_common([
                sa.Column("report_id", sa.String(60), nullable=False),
                sa.Column("patient_ref", sa.String(80), nullable=True),
                sa.Column("patient_name", sa.String(120), nullable=False),
                sa.Column("doctor_name", sa.String(120), nullable=True),
                sa.Column("test_type", sa.String(120), nullable=False),
                sa.Column("completion_date", sa.Date(), nullable=False),
                sa.Column("status", sa.String(30), nullable=False),
                sa.Column("verified_by", sa.String(120), nullable=True),
                sa.Column("template", sa.String(40), nullable=False),
            ])
        )
        op.create_index("ix_lab_report_records_report_id", "lab_report_records", ["report_id"], unique=True)

    if not _table_exists(conn, "lab_report_ready_tests"):
        op.create_table(
            "lab_report_ready_tests",
            *_add_common([
                sa.Column("source_test_id", sa.String(60), nullable=False),
                sa.Column("patient_ref", sa.String(80), nullable=True),
                sa.Column("patient_name", sa.String(120), nullable=False),
                sa.Column("doctor_name", sa.String(120), nullable=True),
                sa.Column("test_type", sa.String(120), nullable=False),
                sa.Column("completed_on", sa.Date(), nullable=False),
            ])
        )
        op.create_index("ix_lab_report_ready_tests_source_test_id", "lab_report_ready_tests", ["source_test_id"], unique=True)

    if not _table_exists(conn, "lab_result_access_grants"):
        op.create_table(
            "lab_result_access_grants",
            *_add_common([
                sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
                sa.Column("patient_ref", sa.String(80), nullable=False),
                sa.Column("patient_name", sa.String(120), nullable=False),
                sa.Column("doctor_name", sa.String(120), nullable=True),
                sa.Column("email", sa.String(255), nullable=False),
                sa.Column("phone", sa.String(30), nullable=True),
                sa.Column("access_type", sa.String(20), nullable=False),
                sa.Column("status", sa.String(20), nullable=False),
                sa.Column("access_count", sa.Integer(), nullable=False),
                sa.Column("access_code", sa.String(40), nullable=False),
                sa.Column("expiry_date", sa.String(20), nullable=True),
                sa.Column("last_access", sa.String(40), nullable=True),
            ])
        )
        op.create_index("ix_lab_result_access_grants_grant_id", "lab_result_access_grants", ["grant_id"], unique=True)

    if not _table_exists(conn, "lab_result_access_logs"):
        op.create_table(
            "lab_result_access_logs",
            *_add_common([
                sa.Column("patient_ref", sa.String(80), nullable=True),
                sa.Column("patient_name", sa.String(120), nullable=False),
                sa.Column("accessed_by", sa.String(255), nullable=False),
                sa.Column("doctor_name", sa.String(120), nullable=True),
                sa.Column("access_time", sa.String(40), nullable=False),
                sa.Column("action", sa.String(60), nullable=False),
                sa.Column("ip_address", sa.String(64), nullable=False),
                sa.Column("device_browser", sa.String(120), nullable=False),
            ])
        )

    if not _table_exists(conn, "lab_test_categories"):
        op.create_table(
            "lab_test_categories",
            *_add_common([sa.Column("category_name", sa.String(120), nullable=False)])
        )
        op.create_index("ix_lab_test_categories_category_name", "lab_test_categories", ["category_name"], unique=True)

    if not _table_exists(conn, "lab_catalogue_tests"):
        op.create_table(
            "lab_catalogue_tests",
            *_add_common([
                sa.Column("test_code", sa.String(40), nullable=False),
                sa.Column("test_name", sa.String(160), nullable=False),
                sa.Column("category", sa.String(120), nullable=False),
                sa.Column("sample_type", sa.String(60), nullable=False),
                sa.Column("turnaround_time", sa.String(60), nullable=False),
                sa.Column("price_inr", sa.DECIMAL(10, 2), nullable=False),
                sa.Column("parameters_count", sa.Integer(), nullable=False),
                sa.Column("status", sa.String(20), nullable=False),
                sa.Column("test_instructions", sa.Text(), nullable=True),
            ])
        )
        op.create_index("ix_lab_catalogue_tests_test_code", "lab_catalogue_tests", ["test_code"], unique=True)

    if not _table_exists(conn, "lab_qc_runs"):
        op.create_table(
            "lab_qc_runs",
            *_add_common([
                sa.Column("qc_id", sa.String(60), nullable=False),
                sa.Column("test", sa.String(120), nullable=False),
                sa.Column("qc_material", sa.String(120), nullable=False),
                sa.Column("lot_number", sa.String(80), nullable=False),
                sa.Column("run_date", sa.String(20), nullable=False),
                sa.Column("operator", sa.String(120), nullable=False),
                sa.Column("status", sa.String(20), nullable=False),
                sa.Column("observed_value", sa.DECIMAL(10, 3), nullable=False),
            ])
        )
        op.create_index("ix_lab_qc_runs_qc_id", "lab_qc_runs", ["qc_id"], unique=True)

    if not _table_exists(conn, "lab_qc_materials"):
        op.create_table(
            "lab_qc_materials",
            *_add_common([
                sa.Column("material_name", sa.String(160), nullable=False),
                sa.Column("material_type", sa.String(80), nullable=False),
                sa.Column("manufacturer", sa.String(120), nullable=False),
                sa.Column("lot_number", sa.String(80), nullable=False),
                sa.Column("expiry_date", sa.String(20), nullable=False),
                sa.Column("storage", sa.String(40), nullable=False),
                sa.Column("quantity", sa.Integer(), nullable=False),
            ])
        )

    if not _table_exists(conn, "lab_qc_rules"):
        op.create_table(
            "lab_qc_rules",
            *_add_common([
                sa.Column("rule_name", sa.String(120), nullable=False),
                sa.Column("description", sa.Text(), nullable=False),
                sa.Column("rule_type", sa.String(80), nullable=False),
                sa.Column("action_required", sa.String(160), nullable=False),
                sa.Column("priority", sa.String(20), nullable=False),
            ])
        )

    if not _table_exists(conn, "lab_profile_configs"):
        op.create_table(
            "lab_profile_configs",
            *_add_common([
                sa.Column("lab_id", sa.String(40), nullable=False),
                sa.Column("lab_name", sa.String(180), nullable=False),
                sa.Column("lab_type", sa.String(120), nullable=False),
                sa.Column("registration_number", sa.String(80), nullable=False),
                sa.Column("established_date", sa.String(20), nullable=False),
                sa.Column("accreditation", sa.String(120), nullable=False),
                sa.Column("accreditation_number", sa.String(120), nullable=True),
                sa.Column("address", sa.String(255), nullable=True),
                sa.Column("city", sa.String(100), nullable=True),
                sa.Column("state", sa.String(100), nullable=True),
                sa.Column("pincode", sa.String(20), nullable=True),
                sa.Column("phone", sa.String(30), nullable=True),
                sa.Column("emergency_phone", sa.String(30), nullable=True),
                sa.Column("email", sa.String(255), nullable=True),
                sa.Column("website", sa.String(255), nullable=True),
            ])
        )


def downgrade() -> None:
    for t in [
        "lab_profile_configs",
        "lab_qc_rules",
        "lab_qc_materials",
        "lab_qc_runs",
        "lab_catalogue_tests",
        "lab_test_categories",
        "lab_result_access_logs",
        "lab_result_access_grants",
        "lab_report_ready_tests",
        "lab_report_records",
        "lab_sample_tracking",
        "lab_critical_alerts",
        "lab_test_registrations",
    ]:
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))
