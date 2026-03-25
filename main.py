"""
Main FastAPI application entry point.
Hospital Management SaaS Platform with zero-intervention database setup.
"""
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
import logging
import time
import uuid
import asyncio
import os
from alembic.config import Config
from alembic import command
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError, DBAPIError

from app.core.config import settings
from app.database.session import init_database, close_database, get_db_session, get_async_engine
from app.middleware.tenant_isolation import TenantIsolationMiddleware
from app.middleware.clinical_audit import ClinicalAuditMiddleware
from app.api.v1.api import api_router
from app.core.exceptions import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler,
    business_logic_exception_handler,
    integrity_error_handler,
    operational_error_handler,
    dbapi_error_handler,
    BusinessLogicError
)

# Configure logging with detailed format for debugging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

# Global lock for migrations and seeding
_setup_completed = False
_setup_lock = asyncio.Lock()


def acquire_advisory_lock(db_url: str, lock_id: int) -> bool:
    """Acquire PostgreSQL advisory lock for concurrency safety"""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id})
        acquired = result.scalar()
        logger.info(f"Advisory lock {lock_id} acquired: {acquired}")
        return bool(acquired)


def release_advisory_lock(db_url: str, lock_id: int):
    """Release PostgreSQL advisory lock"""
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
            logger.info(f"Advisory lock {lock_id} released")
    except Exception as e:
        logger.error(f"Failed to release advisory lock: {e}")


async def seed_superadmin():
    """Create Super Admin user and all required roles if they don't exist - bulletproof and idempotent"""
    logger.info("👤 Starting SuperAdmin and roles seed process...")
    
    try:
        # Create a direct session instead of using the generator
        from app.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            from app.models.user import User, Role, user_roles
            from app.models.tenant import Hospital
            from app.core.security import SecurityManager
            from sqlalchemy import select, func
            
            try:
                # Step 1: Create all required roles first
                logger.info("Creating required roles...")
                required_roles = [
                    {"name": "SUPER_ADMIN", "display_name": "Super Administrator", "description": "Platform Super Administrator", "level": 100},
                    {"name": "HOSPITAL_ADMIN", "display_name": "Hospital Administrator", "description": "Hospital Administrator", "level": 90},
                    {"name": "DOCTOR", "display_name": "Doctor", "description": "Medical Doctor", "level": 80},
                    {"name": "PATHOLOGIST", "display_name": "Pathologist", "description": "Pathologist - signs off on lab results", "level": 78},
                    {"name": "NURSE", "display_name": "Nurse", "description": "Registered Nurse", "level": 70},
                    {"name": "PHARMACIST", "display_name": "Pharmacist", "description": "Licensed Pharmacist", "level": 65},
                    {"name": "LAB_ADMIN", "display_name": "Lab Administrator", "description": "Laboratory Department Administrator", "level": 64},
                    {"name": "LAB_SUPERVISOR", "display_name": "Lab Supervisor", "description": "Laboratory Supervisor - verifies and releases results", "level": 63},
                    {"name": "LAB_TECH", "display_name": "Lab Technician", "description": "Laboratory Technician", "level": 62},
                    {"name": "RECEPTIONIST", "display_name": "Receptionist", "description": "Front Desk Receptionist", "level": 60},
                    {"name": "STAFF", "display_name": "Staff", "description": "General Staff Member", "level": 50},
                    {"name": "PATIENT", "display_name": "Patient", "description": "Hospital Patient", "level": 10},
                ]
                
                role_ids = {}
                roles_created = 0
                
                for role_data in required_roles:
                    role_query = select(Role).where(Role.name == role_data["name"])
                    role_result = await db.execute(role_query)
                    existing_role = role_result.scalar_one_or_none()
                    
                    if not existing_role:
                        logger.info(f"Creating {role_data['name']} role...")
                        role = Role(
                            id=uuid.uuid4(),
                            name=role_data["name"],
                            display_name=role_data["display_name"],
                            description=role_data["description"],
                            level=role_data["level"]
                        )
                        db.add(role)
                        await db.flush()
                        role_ids[role_data["name"]] = role.id
                        roles_created += 1
                        logger.info(f"{role_data['name']} role created: {role.id}")
                    else:
                        role_ids[role_data["name"]] = existing_role.id
                        logger.info(f"{role_data['name']} role found: {existing_role.id}")
                
                # Commit roles first to ensure they're persisted
                if roles_created > 0:
                    await db.commit()
                    logger.info(f"Committed {roles_created} new roles to database")
                
                # Step 2: Check if Super Admin already exists
                security = SecurityManager()
                superadmin_email = (settings.SUPERADMIN_EMAIL or "").strip().lower()
                logger.info(f"Checking if SuperAdmin exists: {superadmin_email}")
                existing_admin_query = select(User).where(func.lower(User.email) == superadmin_email)
                existing_admin_result = await db.execute(existing_admin_query)
                existing_admin = existing_admin_result.scalar_one_or_none()
                
                if existing_admin:
                    logger.info(f"SuperAdmin already exists, skipping creation: {superadmin_email}")
                    # Keep superadmin credentials in sync with environment for deployments.
                    # This avoids login lockout when DB is reused but SUPERADMIN_PASSWORD changes.
                    if settings.SUPERADMIN_PASSWORD:
                        password_ok = security.verify_password(
                            settings.SUPERADMIN_PASSWORD,
                            existing_admin.password_hash,
                        )
                        if not password_ok:
                            logger.info("SuperAdmin password mismatch detected; syncing from environment")
                            existing_admin.password_hash = security.hash_password(settings.SUPERADMIN_PASSWORD)
                            await db.commit()
                            logger.info("SuperAdmin password synced successfully")
                    # Verify all roles exist
                    logger.info(f"Verifying all {len(required_roles)} roles exist...")
                    all_roles_query = select(Role)
                    all_roles_result = await db.execute(all_roles_query)
                    all_roles = all_roles_result.scalars().all()
                    logger.info(f"Found {len(all_roles)} roles in database: {[r.name for r in all_roles]}")
                    return
                
                logger.info("SuperAdmin not found, creating new SuperAdmin...")
                
                # Step 3: Get or create platform hospital
                logger.info("Checking for Platform Hospital...")
                platform_hospital_query = select(Hospital).where(Hospital.name == 'Platform Hospital')
                platform_hospital_result = await db.execute(platform_hospital_query)
                existing_hospital = platform_hospital_result.scalar_one_or_none()
                
                if not existing_hospital:
                    logger.info("🏗️ Creating Platform Hospital...")
                    hospital = Hospital(
                        id=uuid.uuid4(),
                        name="Platform Hospital",
                        registration_number="PLATFORM_001",
                        email="platform@hsm.com",
                        phone="+1000000000",
                        address="Platform Address",
                        city="Platform City",
                        state="Platform State",
                        country="Platform Country",
                        pincode="00000"
                    )
                    db.add(hospital)
                    await db.flush()
                    hospital_id = hospital.id
                    logger.info(f" Platform Hospital created: {hospital_id}")
                else:
                    hospital_id = existing_hospital.id
                    logger.info(f" Platform Hospital found: {hospital_id}")
                
                # Step 4: Hash password using project's security manager
                logger.info(" Hashing SuperAdmin password...")
                password_hash = security.hash_password(settings.SUPERADMIN_PASSWORD)
                
                # Step 5: Create Super Admin user with insert-if-not-exists logic
                logger.info(" Creating SuperAdmin user...")
                user = User(
                    id=uuid.uuid4(),
                    hospital_id=hospital_id,
                    email=superadmin_email,
                    phone="+1000000000",
                    password_hash=password_hash,
                    first_name=settings.SUPERADMIN_FIRST_NAME,
                    last_name=settings.SUPERADMIN_LAST_NAME,
                    status="ACTIVE",
                    email_verified=True
                )
                db.add(user)
                await db.flush()
                logger.info(f" SuperAdmin user created: {user.id}")
                
                # Step 6: Assign SUPER_ADMIN role
                logger.info(" Assigning SUPER_ADMIN role...")
                role_assignment = user_roles.insert().values(
                    user_id=user.id, 
                    role_id=role_ids["SUPER_ADMIN"]
                )
                await db.execute(role_assignment)
                
                # Step 7: Commit transaction
                logger.info(" Committing SuperAdmin and roles creation...")
                await db.commit()
                
                logger.info("SuperAdmin and roles created successfully!")
                logger.info(f" Email: {superadmin_email}")
                logger.info(f" Password: {settings.SUPERADMIN_PASSWORD}")
                logger.info(f" User ID: {user.id}")
                logger.info(f" Roles created: {list(role_ids.keys())}")
                
            except IntegrityError as e:
                # Handle unique constraint violation gracefully
                logger.info(" Integrity error during SuperAdmin creation (likely concurrent creation)")
                await db.rollback()
                
                # Double-check if user was created by another process
                check_query = select(User).where(func.lower(User.email) == superadmin_email)
                check_result = await db.execute(check_query)
                if check_result.scalar_one_or_none():
                    logger.info(" SuperAdmin already exists (created by concurrent process), skipping")
                else:
                    logger.exception(f" Unexpected integrity error during SuperAdmin creation: {e}")
                    raise
                    
            except Exception as e:
                logger.exception(f" Error during SuperAdmin creation transaction: {e}")
                await db.rollback()
                raise
                
    except Exception as e:
        logger.exception(f" SuperAdmin seed process failed: {e}")
        raise


async def create_pharmacy_tables_if_needed():
    """Create ALL pharmacy tables if they don't exist (bypass migration cycle issue)"""
    try:
        from sqlalchemy import text
        from app.models.base import TenantBaseModel
        from app.models.pharmacy import (
            Medicine, Supplier, PurchaseOrder, PurchaseOrderItem,
            GoodsReceipt, GoodsReceiptItem, StockBatch, StockLedger,
            Sale, SaleItem, Return, ReturnItem, ExpiryAlert
        )
        
        logger.info(" Checking pharmacy tables...")
        
        engine = get_async_engine()
        async with engine.begin() as conn:
            # Use SQLAlchemy metadata to create all pharmacy tables
            # This automatically creates all tables defined in pharmacy models
            await conn.run_sync(TenantBaseModel.metadata.create_all, checkfirst=True)
            logger.info(" All pharmacy tables created/verified successfully")
                    
    except Exception as e:
        logger.error(f" Error creating pharmacy tables: {e}")
        # Don't raise - allow app to start even if pharmacy tables fail
        # The error will be caught when trying to use pharmacy endpoints


async def create_all_tables_from_models():
    """
    Create all tables defined in SQLAlchemy models (no Alembic).
    This lets us bring up a fresh database just by running main.py.
    """
    try:
        import app.models  # noqa: F401 — register all models (including DemoRequest) on Base.metadata
        from app.database.base import Base

        logger.info(" Creating all tables from SQLAlchemy models (DB_BOOTSTRAP_FROM_MODELS=True)...")
        engine = get_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
        logger.info(" All tables created/verified successfully from models")
    except Exception as e:
        logger.error(f" Error creating tables from models: {e}")
        raise


async def _run_bed_charge_scheduler():
    """
    Scheduled task that runs at midnight daily to auto-post IPD bed charges.
    FIX: Previously this required a manual API call every day — missed days
    meant lost room revenue.
    """
    import datetime as _dt
    while True:
        try:
            now = _dt.datetime.utcnow()
            # Calculate seconds until next midnight UTC
            next_midnight = (now + _dt.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait_seconds = (next_midnight - now).total_seconds()
            logger.info(
                f"IPD bed charge scheduler: next run in {wait_seconds:.0f}s "
                f"(at {next_midnight} UTC)"
            )
            await asyncio.sleep(wait_seconds)
            # Run bed charges for all active admissions
            await _post_daily_bed_charges()
        except asyncio.CancelledError:
            logger.info("IPD bed charge scheduler cancelled")
            break
        except Exception as e:
            logger.error(f"IPD bed charge scheduler error: {e}")
            # Retry in 1 hour on error
            await asyncio.sleep(3600)


async def _post_daily_bed_charges():
    """Post daily bed charges for all active IPD admissions across all hospitals."""
    from app.database.session import AsyncSessionLocal
    from app.models.patient import Admission
    from app.models.billing.bill import Bill
    from sqlalchemy import select, and_
    import datetime as _dt

    logger.info("Running daily IPD bed charge posting...")
    posted = 0
    today = _dt.date.today()

    async with AsyncSessionLocal() as db:
        # Find all active admissions with an associated DRAFT or active bill
        admissions_result = await db.execute(
            select(Admission).where(Admission.is_active == True)
        )
        admissions = admissions_result.scalars().all()
        for admission in admissions:
            try:
                # Find corresponding IPD bill in DRAFT or PARTIALLY_PAID status
                bill_result = await db.execute(
                    select(Bill).where(
                        and_(
                            Bill.admission_id == admission.id,
                            Bill.bill_type == "IPD",
                            Bill.status.in_(["DRAFT", "PARTIALLY_PAID"]),
                        )
                    ).limit(1)
                )
                bill = bill_result.scalar_one_or_none()
                if bill:
                    from app.models.billing.bill import BillItem
                    import uuid
                    # Default bed rate — real implementation pulls from ward/bed config
                    bed_rate = 500.0  # ₹500/day placeholder
                    charge = BillItem(
                        id=uuid.uuid4(),
                        bill_id=bill.id,
                        description=f"Bed charge — {today.isoformat()}",
                        quantity=1,
                        unit_price=bed_rate,
                        tax_percentage=0,
                        line_subtotal=bed_rate,
                        line_tax=0,
                        line_total=bed_rate,
                    )
                    db.add(charge)
                    bill.total_amount = float(bill.total_amount or 0) + bed_rate
                    bill.balance_due = float(bill.balance_due or 0) + bed_rate
                    posted += 1
            except Exception as e:
                logger.error(f"Bed charge failed for admission {admission.id}: {e}")
        await db.commit()
    logger.info(f"Daily bed charge posting complete: {posted} admission(s) charged")


async def sync_superadmin_credentials():
    """
    Ensure SUPERADMIN_EMAIL/SUPERADMIN_PASSWORD from environment
    are applied to the existing superadmin user.
    Runs independently from migration lock flow.
    """
    from app.database.session import AsyncSessionLocal
    from app.models.user import User
    from app.core.security import SecurityManager
    from sqlalchemy import select, func

    superadmin_email = (settings.SUPERADMIN_EMAIL or "").strip().lower()
    if not superadmin_email or not settings.SUPERADMIN_PASSWORD:
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(func.lower(User.email) == superadmin_email).limit(1)
        )
        user = result.scalar_one_or_none()
        if not user:
            logger.warning("SuperAdmin sync skipped: user not found in database yet")
            return

        security = SecurityManager()
        if not security.verify_password(settings.SUPERADMIN_PASSWORD, user.password_hash):
            user.password_hash = security.hash_password(settings.SUPERADMIN_PASSWORD)
            await db.commit()
            logger.info("SuperAdmin password synced from environment")


async def lifespan(app: FastAPI):
    """Application lifespan with single setup function"""
    logger.info(" Starting Hospital Management SaaS application...")
    db_ready = False
    try:
        db_ready = await setup_database_once()
        if db_ready:
            try:
                await sync_superadmin_credentials()
            except Exception as sync_ex:
                logger.warning(f"SuperAdmin credential sync skipped: {sync_ex}")
            logger.info(" Application startup complete - Ready to serve requests")
        else:
            logger.warning(" Application started in degraded mode (database setup incomplete)")
    except Exception as e:
        logger.exception(f" Database unavailable during startup, running in degraded mode: {e}")

    # ── FIX: Start notification worker as background task ──────────────────
    # Previously the notification worker was a separate manual script
    # (scripts/run_notification_worker.py). This caused missed notifications
    # whenever the script was not started manually.
    # Now it runs automatically as part of the app lifecycle.
    _worker_task = None
    if db_ready:
        try:
            from app.services.notifications.worker import start_worker_background
            _worker_task = start_worker_background(interval_seconds=30, batch_size=50)
            logger.info("✓ Notification worker background task started")
        except Exception as worker_ex:
            logger.error(f"✗ Notification worker failed to start: {worker_ex}")
    else:
        logger.warning("! Notification worker not started (database unavailable)")

    # ── FIX: Start IPD daily bed charge scheduler ──────────────────────────
    # Previously bed charges required a manual API call each day.
    # This scheduler runs at midnight and auto-posts daily bed charges.
    _bed_charge_task = None
    if db_ready:
        try:
            _bed_charge_task = asyncio.create_task(_run_bed_charge_scheduler())
            logger.info("✓ IPD daily bed charge scheduler started")
        except Exception as sched_ex:
            logger.error(f"✗ Bed charge scheduler failed to start: {sched_ex}")
    else:
        logger.warning("! Bed charge scheduler not started (database unavailable)")

    yield
    
    # Shutdown
    logger.info("Shutting down application...")
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
    if _bed_charge_task and not _bed_charge_task.done():
        _bed_charge_task.cancel()
    await close_database()
    logger.info("Application shutdown complete")


async def setup_database_once() -> bool:
    """Single database setup function with strict ordering"""
    global _setup_completed
    
    async with _setup_lock:
        if _setup_completed:
            logger.info(" Database setup already completed in this process")
            return True
        
        # Use PostgreSQL advisory lock for cross-process safety
        MIGRATION_LOCK_ID = 123456789
        lock_acquired = acquire_advisory_lock(settings.DATABASE_URL_SYNC, MIGRATION_LOCK_ID)
        
        if not lock_acquired:
            logger.info(" Another process is running setup; skipping in this process.")
            _setup_completed = True
            return True
        
        try:
            # Step 1: Test database connection
            logger.info("🔌 Testing database connection...")
            await init_database()
            logger.info("DB verified")
            
            # Step 2: Either run Alembic migrations OR bootstrap directly from models
            if settings.DB_BOOTSTRAP_FROM_MODELS:
                logger.info(" DB_BOOTSTRAP_FROM_MODELS=True -> skipping Alembic; creating tables from models")
                await create_all_tables_from_models()
            else:
                logger.info(" MIGRATIONS START")
                await asyncio.to_thread(run_migrations_isolated)  # IMPORTANT
                logger.info(" MIGRATIONS FINISHED")
                
                # Create pharmacy tables (bypass migration cycle issue)
                logger.info(" PHARMACY TABLES START")
                await create_pharmacy_tables_if_needed()
                logger.info(" PHARMACY TABLES FINISHED")

            # Step 2.5: Optionally prune legacy tables (dev/local only)
            if settings.DB_PRUNE_UNUSED_TABLES:
                logger.info(" PRUNE UNUSED TABLES START")
                await asyncio.to_thread(prune_unused_tables)
                logger.info(" PRUNE UNUSED TABLES FINISHED")
            
            # Step 3: Seed SuperAdmin ONLY after migrations are 100% complete
            logger.info(" SEED START")
            try:
                await seed_superadmin()
                logger.info(" SEED FINISHED")
            except Exception as seed_error:
                logger.exception(f" SuperAdmin seeding failed: {seed_error}")
                raise
            
            _setup_completed = True
            logger.info(" Database setup completed successfully")
            return True
            
        except Exception as e:
            logger.exception(f" Database setup failed: {e}")
            return False
        finally:
            # Always release the advisory lock
            try:
                release_advisory_lock(settings.DATABASE_URL_SYNC, MIGRATION_LOCK_ID)
            except Exception as lock_error:
                logger.error(f" Failed to release advisory lock: {lock_error}")
                # Don't re-raise lock release errors


def run_migrations_isolated():
    """Run Alembic migrations using subprocess - COMPLETE ISOLATION"""
    import subprocess
    try:
        logger.info("Starting completely isolated Alembic migrations...")
        
        # Run alembic as subprocess - complete isolation from application
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            logger.info("Isolated migrations completed successfully")
            if result.stdout.strip():
                logger.info(f"Migration output: {result.stdout.strip()}")
            return True
        else:
            logger.error(f" Migration failed with return code {result.returncode}")
            if result.stdout.strip():
                logger.error(f"STDOUT: {result.stdout.strip()}")
            if result.stderr.strip():
                logger.error(f"STDERR: {result.stderr.strip()}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(" Migration timed out after 2 minutes")
        return False
    except Exception as e:
        logger.exception(f" Migration failed: {e}")
        return False


KEEP_TABLES = {
    "admissions",
    "appointments",
    "audit_logs",
    "beds",
    "bill_items",
    "bills",
    "chain_of_custody",
    "compliance_exports",
    "contact_messages",
    "departments",
    "demo_requests",
    "discharge_summaries",
    "doctor_profiles",
    "doctor_schedules",
    "equipment_maintenance_logs",
    "finance_audit_logs",
    "financial_documents",
    "hospital_subscriptions",
    "hospitals",
    "insurance_claims",
    "ipd_charges",
    "lab_audit_logs",
    "lab_equipment",
    "lab_equipment_test_map",
    "lab_order_items",
    "lab_orders",
    "lab_reports",
    "lab_samples",
    "lab_test_categories",
    "lab_tests",
    "medical_records",
    "notification_outbox",
    "nurse_profiles",
    "password_history",
    "patient_documents",
    "patient_profiles",
    "payments",
    "permissions",
    "pharmacy_expiry_alerts",
    "pharmacy_grn_items",
    "pharmacy_grns",
    "pharmacy_medicines",
    "pharmacy_purchase_order_items",
    "pharmacy_purchase_orders",
    "pharmacy_return_items",
    "pharmacy_returns",
    "pharmacy_sale_items",
    "pharmacy_sales",
    "pharmacy_stock_batches",
    "pharmacy_stock_ledger",
    "pharmacy_suppliers",
    "prescription_integrations",
    "prescription_lab_orders",
    "prescription_medicines",
    "prescription_notifications",
    "prescription_pdfs",
    "prescriptions",
    "qc_corrective_actions",
    "qc_rules",
    "qc_runs",
    "receptionist_profiles",
    "reconciliations",
    "refunds",
    "report_access_logs",
    "report_share_tokens",
    "result_values",
    "role_permissions",
    "roles",
    "sample_order_items",
    "service_items",
    "staff_department_assignments",
    "staff_profiles",
    "subscription_plans",
    "support_tickets",
    "surgery_cases",
    "surgery_documentation",
    "surgery_team_members",
    "surgery_video_view_audits",
    "surgery_videos",
    "tax_profiles",
    "tele_appointments",
    "tele_prescriptions",
    "telemed_consultation_notes",
    "telemed_files",
    "telemed_messages",
    "telemed_notifications",
    "telemed_participants",
    "telemed_provider_config",
    "telemed_sessions",
    "telemed_vitals",
    "test_results",
    "treatment_plans",
    "user_roles",
    "users",
    "wards",
}


def prune_unused_tables():
    """
    Drop legacy tables that are not in KEEP_TABLES.
    Intended for local/dev when recreating DB from scratch.
    """
    if not settings.DB_PRUNE_UNUSED_TABLES:
        logger.info("DB_PRUNE_UNUSED_TABLES is False; skipping prune step")
        return

    logger.info("Pruning legacy tables not used by current models...")
    try:
        engine = create_engine(settings.DATABASE_URL_SYNC)
        with engine.connect() as conn:
            res = conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            all_tables = {row[0] for row in res}
            extra_tables = sorted(t for t in all_tables if t not in KEEP_TABLES)
            if not extra_tables:
                logger.info("No extra tables found to prune.")
                return

            logger.info(f"Found {len(extra_tables)} extra table(s) to drop: {extra_tables}")
            for tbl in extra_tables:
                logger.info(f"Dropping legacy table: {tbl}")
                conn.execute(text(f'DROP TABLE IF EXISTS "{tbl}" CASCADE'))
            conn.commit()
            logger.info("Legacy table prune completed.")
    except Exception as e:
        logger.exception(f"Failed to prune legacy tables: {e}")


# Tag order for Swagger UI - Authentication first, then Super Admin
_openapi_tags = [
    {"name": "Authentication", "description": "User authentication and authorization endpoints"},
    {"name": "Super Admin - Hospital Management", "description": "Platform-wide hospital CRUD"},
    {"name": "Super Admin - Hospital Administrator Management", "description": "Manage hospital admins"},
    {"name": "Super Admin - Subscription Plan Management", "description": "Free, Standard, Premium plans"},
    {"name": "Super Admin - Hospital Subscription Management", "description": "Assign plans to hospitals"},
    {"name": "Super Admin - Support Management", "description": "Helpdesk and escalations"},
    {"name": "Super Admin - Analytics & Monitoring", "description": "Dashboard, revenue, audit logs"},
    {"name": "Super Admin - Notifications", "description": "Notify hospital admins"},
    {"name": "Analytics", "description": "Platform analytics: dashboard, revenue, audit logs (Super Admin)"},
]

# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Multi-tenant Hospital Management SaaS Platform with Zero-Intervention Database Setup",
    docs_url="/docs" if settings.OPENAPI_DOCS else None,
    redoc_url="/redoc" if settings.OPENAPI_DOCS else None,
    openapi_tags=_openapi_tags,
    lifespan=lifespan
)


@app.on_event("startup")
async def startup_event():
    """Startup hook for deployment diagnostics."""
    logger.info("FastAPI startup event triggered")


# Use default OpenAPI - all endpoints show in Swagger (no custom filtering)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add tenant isolation middleware
app.add_middleware(TenantIsolationMiddleware)

# FIX: Add clinical audit trail middleware for HIPAA compliance
app.add_middleware(ClinicalAuditMiddleware)

# Add global exception handlers (order matters: more specific first)
app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(OperationalError, operational_error_handler)
app.add_exception_handler(DBAPIError, dbapi_error_handler)
app.add_exception_handler(Exception, general_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(BusinessLogicError, business_logic_exception_handler)
from fastapi import HTTPException
app.add_exception_handler(HTTPException, http_exception_handler)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Request logging and correlation ID middleware"""
    correlation_id = str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    
    start_time = time.time()
    logger.info(
        f"Request started",
        extra={
            "correlation_id": correlation_id,
            "method": request.method,
            "url": str(request.url),
            "client_ip": request.client.host if request.client else None,
        }
    )
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    logger.info(
        f"Request completed",
        extra={
            "correlation_id": correlation_id,
            "status_code": response.status_code,
            "duration": f"{duration:.3f}s",
        }
    )
    
    response.headers["X-Correlation-ID"] = correlation_id
    return response


@app.get("/health")
async def health_check():
    """Health check endpoint with live database connectivity verification"""
    from app.schemas.response import SuccessResponse

    db_ok = False
    db_error = None

    try:
        # Lightweight connectivity check - consume result for driver compatibility
        engine = get_async_engine()
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.fetchone()
        db_ok = True
    except Exception as e:
        db_error = str(e)
        logger.exception("Database health check failed")

    return SuccessResponse(
        success=True,
        message="Application health status",
        data={
            "status": "healthy" if db_ok else "degraded",
            "version": settings.APP_VERSION,
            "timestamp": time.time(),
            "database": "connected" if db_ok else "unreachable",
            "database_error": db_error,
            "migrations": "auto-applied",
            "superadmin": "auto-seeded"
        }
    ).dict()


@app.get("/admin/verify")
async def verify_superadmin():
    """Verify Super Admin exists"""
    from app.database.session import AsyncSessionLocal
    from app.schemas.response import SuccessResponse
    async with AsyncSessionLocal() as db:
        from app.models.user import User
        from sqlalchemy import select, func
        
        admin_count = await db.execute(
            select(func.count(User.id)).where(
                func.lower(User.email) == (settings.SUPERADMIN_EMAIL or "").strip().lower()
            )
        )
        count = admin_count.scalar()
        
        return SuccessResponse(
            success=True,
            message="Super Admin verification complete",
            data={
                "superadmin_email": settings.SUPERADMIN_EMAIL,
                "exists": count > 0,
                "count": count
            }
        ).dict()


# Include API routers
app.include_router(api_router)

# Public demo request (DCM) — mounted after main API
from app.api.demo_public import router as demo_public_router
from app.api.contact_public import router as contact_public_router
from app.api.notifications_root import router as notifications_root_router

app.include_router(demo_public_router)
app.include_router(contact_public_router)
app.include_router(notifications_root_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8060,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )