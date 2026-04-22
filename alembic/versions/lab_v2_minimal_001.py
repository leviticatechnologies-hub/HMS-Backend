"""Lab v2 minimal: drop legacy lab tables, keep equipment + maintenance only.

Revision ID: lab_v2_minimal_001
Revises: opd_management_001
Create Date: 2026-04-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "lab_v2_minimal_001"
down_revision = "opd_management_001"
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    return name in inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()

    # Prescription lab orders: drop FK to lab_tests (column kept as plain UUID)
    op.execute(
        "ALTER TABLE IF EXISTS prescription_lab_orders "
        "DROP CONSTRAINT IF EXISTS prescription_lab_orders_lab_test_id_fkey;"
    )

    legacy = [
        "lab_equipment_test_map",
        "qc_corrective_actions",
        "report_access_logs",
        "report_share_tokens",
        "result_values",
        "test_results",
        "chain_of_custody",
        "sample_order_items",
        "lab_samples",
        "lab_reports",
        "lab_order_items",
        "lab_orders",
        "equipment_maintenance_logs",
        "qc_runs",
        "lab_audit_logs",
        "compliance_exports",
        "qc_rules",
        "lab_tests",
        "lab_test_categories",
        "lab_equipment",
    ]
    for t in legacy:
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))

    if not _table_exists(conn, "lab_equipment"):
        op.create_table(
            "lab_equipment",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("equipment_code", sa.String(length=50), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("category", sa.String(length=20), nullable=False),
            sa.Column("manufacturer", sa.String(length=100), nullable=True),
            sa.Column("model", sa.String(length=100), nullable=True),
            sa.Column("serial_number", sa.String(length=100), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("installation_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_calibrated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_calibration_due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("location", sa.String(length=100), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("specifications", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("hospital_id", "equipment_code", name="uq_equipment_code_per_hospital"),
        )
        op.create_index("ix_lab_equipment_hospital_id", "lab_equipment", ["hospital_id"])
        op.create_index("ix_lab_equipment_equipment_code", "lab_equipment", ["equipment_code"])

    if not _table_exists(conn, "equipment_maintenance_logs"):
        op.create_table(
            "equipment_maintenance_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("equipment_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("type", sa.String(length=20), nullable=False),
            sa.Column("performed_by", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("remarks", sa.Text(), nullable=True),
            sa.Column("attachment_ref", sa.String(length=500), nullable=True),
            sa.Column("cost", sa.DECIMAL(10, 2), nullable=True),
            sa.Column("service_provider", sa.String(length=200), nullable=True),
            sa.Column("service_ticket_no", sa.String(length=100), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["equipment_id"], ["lab_equipment.id"]),
            sa.ForeignKeyConstraint(["performed_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_equipment_maintenance_logs_equipment_id", "equipment_maintenance_logs", ["equipment_id"]
        )


def downgrade() -> None:
    raise NotImplementedError("lab_v2_minimal_001 downgrade is not supported (legacy data removed)")
