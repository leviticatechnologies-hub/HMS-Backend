"""
Alembic environment configuration.
ISOLATED - NO application imports that trigger DB connections or seeding.
"""
import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# Load environment variables directly (NO app.core.config import)
load_dotenv()

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import ONLY Base and models - NO config, NO services, NO database session
from app.database.base import Base

# Import models ONLY for metadata registration - NO side effects
from app.models.base import BaseModel, TenantBaseModel
from app.models.tenant import Hospital, SubscriptionPlanModel, HospitalSubscription
from app.models.user import User, Role, Permission, user_roles, role_permissions, AuditLog
from app.models.hospital import Department, StaffProfile, Ward, StaffDepartmentAssignment, Bed
from app.models.doctor import DoctorProfile, Prescription, TreatmentPlan
from app.models.patient import PatientProfile, Appointment, MedicalRecord, Admission, DischargeSummary, PatientDocument
from app.models.nurse import NurseProfile
from app.models.receptionist import ReceptionistProfile
# Billing & Accounts module (SOW)
from app.models.billing import (
    ServiceItem, TaxProfile, Bill, BillItem, IPDCharge,
    BillingPayment, FinancialDocument, InsuranceClaim, Reconciliation, FinanceAuditLog
)
# Payment Gateway module (gateway_payments, payment_receipts, payment_ledger, payment_refunds)
from app.models.payments import Payment, PaymentReceipt, PaymentLedger, PaymentRefund
from app.models.schedule import DoctorSchedule
from app.models.password_history import PasswordHistory

# Import pharmacy models
from app.models.pharmacy import (
    Medicine, Supplier, PurchaseOrder, PurchaseOrderItem,
    GoodsReceipt, GoodsReceiptItem, StockBatch, StockLedger,
    Sale, SaleItem, Return, ReturnItem, ExpiryAlert
)

from app.models.lab import Equipment, EquipmentMaintenanceLog
from app.models.lab_portal import (
    LabTestRegistration,
    LabCriticalAlert,
    LabSampleTracking,
    LabReportRecord,
    LabReportReadyTest,
    LabResultAccessGrant,
    LabResultAccessLog,
    LabTestCategory,
    LabCatalogueTest,
    LabQcRun,
    LabQcMaterial,
    LabQcRule,
    LabProfileConfig,
)
from app.models.prescription import PrescriptionMedicine
from app.models.notifications import (
    NotificationProvider,
    NotificationTemplate,
    NotificationPreference,
    NotificationJob,
    NotificationDeliveryLog,
)
# Surgery module (Phase 1 POA)
from app.models.surgery import (
    SurgeryCase,
    SurgeryTeamMember,
    SurgeryDocumentation,
    SurgeryVideo,
    SurgeryVideoViewAudit,
)
# Support tickets
from app.models.support import SupportTicket

# This is the Alembic Config object
config = context.config

# Get database URL directly from environment - NO settings import.
# Deployments often set only DATABASE_URL (async); derive sync URL the same way as app config.
def _env_async_to_sync(url: str) -> str:
    v = (url or "").strip()
    if v.startswith("postgres://"):
        return v.replace("postgres://", "postgresql://", 1)
    if v.startswith("postgresql+asyncpg://"):
        return v.replace("postgresql+asyncpg://", "postgresql://", 1)
    return v


database_url_sync = (os.getenv("DATABASE_URL_SYNC") or "").strip()
if not database_url_sync:
    async_url = (os.getenv("DATABASE_URL") or "").strip()
    if async_url:
        database_url_sync = _env_async_to_sync(async_url)
if database_url_sync:
    config.set_main_option("sqlalchemy.url", database_url_sync)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata from Base (which now has all models imported above)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using synchronous engine."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()