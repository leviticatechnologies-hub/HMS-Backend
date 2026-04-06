"""hospital tenant_database_name for per-hospital Postgres DBs

Revision ID: hospital_tenant_database_001
Revises: support_tickets_001
Create Date: 2026-04-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "hospital_tenant_database_001"
down_revision = "support_tickets_001"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: startup schema_patches or manual DDL may have added this already (deploy drift).
    conn = op.get_bind()
    insp = inspect(conn)
    cols = {c["name"] for c in insp.get_columns("hospitals")}
    if "tenant_database_name" not in cols:
        op.add_column(
            "hospitals",
            sa.Column("tenant_database_name", sa.String(length=63), nullable=True),
        )
    idx = {i["name"] for i in insp.get_indexes("hospitals")}
    if "ix_hospitals_tenant_database_name" not in idx:
        op.create_index(
            "ix_hospitals_tenant_database_name",
            "hospitals",
            ["tenant_database_name"],
            unique=True,
        )


def downgrade():
    conn = op.get_bind()
    insp = inspect(conn)
    idx = {i["name"] for i in insp.get_indexes("hospitals")}
    if "ix_hospitals_tenant_database_name" in idx:
        op.drop_index("ix_hospitals_tenant_database_name", table_name="hospitals")
    cols = {c["name"] for c in insp.get_columns("hospitals")}
    if "tenant_database_name" in cols:
        op.drop_column("hospitals", "tenant_database_name")
