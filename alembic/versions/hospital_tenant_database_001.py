"""hospital tenant_database_name for per-hospital Postgres DBs

Revision ID: hospital_tenant_database_001
Revises: support_tickets_001
Create Date: 2026-04-06

"""
from alembic import op
import sqlalchemy as sa


revision = "hospital_tenant_database_001"
down_revision = "support_tickets_001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "hospitals",
        sa.Column("tenant_database_name", sa.String(length=63), nullable=True),
    )
    op.create_index(
        "ix_hospitals_tenant_database_name",
        "hospitals",
        ["tenant_database_name"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_hospitals_tenant_database_name", table_name="hospitals")
    op.drop_column("hospitals", "tenant_database_name")
