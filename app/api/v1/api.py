"""
Main API router for Hospital Management SaaS Platform.
Organized by functional areas: Admin, Doctor, Patient, Pharmacy, Lab, Management, Billing & Accounts.
"""
from fastapi import APIRouter, Depends
import logging

from app.core.plan_features import (
    FEATURE_LAB_TESTS,
    FEATURE_PHARMACY,
    FEATURE_VIDEO_CONSULTATION,
)
from app.dependencies.plan_features import require_plan_feature

logger = logging.getLogger(__name__)

_pharmacy_dep = [Depends(require_plan_feature(FEATURE_PHARMACY))]
_telemed_dep = [Depends(require_plan_feature(FEATURE_VIDEO_CONSULTATION))]
_lab_dep = [Depends(require_plan_feature(FEATURE_LAB_TESTS))]

# Create main API router
api_router = APIRouter(prefix="/api/v1")

# ============================================================================
# 1. AUTHENTICATION (Must be first)
# ============================================================================
try:
    from app.api.v1.auth import router as auth_router
    api_router.include_router(auth_router)
    logger.info("✓ Auth router loaded")
except ImportError as e:
    logger.error(f"✗ Failed to load auth router: {e}")

try:
    from app.api.v1.routers.auth_2fa import router as totp_router
    api_router.include_router(totp_router)
    logger.info("✓ 2FA (TOTP) router loaded")
except ImportError as e:
    logger.error(f"✗ Failed to load 2FA router: {e}")

# ============================================================================
# 2. SUPER ADMIN (Second - highest privilege level)
# ============================================================================
try:
    from app.api.v1.routers.admin.super_admin import (
        router as super_admin_router,
        router_super_admin_compat,
    )

    api_router.include_router(super_admin_router)
    api_router.include_router(router_super_admin_compat)
    logger.info("✓ Super Admin router loaded")
except ImportError as e:
    logger.error(f"✗ Failed to load super admin router: {e}")

# ============================================================================
# 3. HOSPITAL ADMIN & ANALYTICS
# ============================================================================
try:
    from app.api.v1.routers.admin.hospital_admin import router as hospital_admin_router
    from app.api.v1.routers.analytics import router as analytics_router
    api_router.include_router(hospital_admin_router)
    api_router.include_router(analytics_router)
    logger.info("✓ Hospital Admin & Analytics routers loaded")
except ImportError as e:
    logger.error(f"✗ Failed to load hospital admin routers: {e}")

# ============================================================================
# 3.5 SUPPORT TICKETS (Staff + Hospital Admin create)
# ============================================================================
try:
    from app.api.v1.routers.support.tickets import router as support_tickets_router
    api_router.include_router(support_tickets_router)
    logger.info("✓ Support Tickets router loaded (/api/v1/support)")
except ImportError as e:
    logger.error(f"✗ Failed to load support tickets router: {e}")

# ============================================================================
# 4. DOCTOR MODULE
# ============================================================================
try:
    from app.api.v1.routers.doctor.doctor_dashboard import router as doctor_dashboard_router
    from app.api.v1.routers.doctor.doctor_management import router as doctor_management_router
    from app.api.v1.routers.doctor.doctor_appointment_tracking import router as doctor_appointment_router
    from app.api.v1.routers.doctor.doctor_patient_records import router as doctor_patient_records_router
    from app.api.v1.routers.doctor.doctor_reports_analytics import router as doctor_reports_router
    from app.api.v1.routers.doctor.doctor_treatment_plans import router as doctor_treatment_router
    from app.api.v1.routers.doctor.simple_prescription import router as simple_prescription_router
    from app.api.v1.routers.doctor.doctor_sidebar import router as doctor_sidebar_router
    
    api_router.include_router(doctor_dashboard_router)
    api_router.include_router(doctor_management_router)
    api_router.include_router(doctor_appointment_router)
    api_router.include_router(doctor_patient_records_router)
    api_router.include_router(doctor_reports_router)
    api_router.include_router(doctor_treatment_router)
    api_router.include_router(simple_prescription_router)
    api_router.include_router(doctor_sidebar_router)
    logger.info("✓ Doctor routers loaded (sidebar + simple-prescription)")
except ImportError as e:
    logger.error(f"✗ Failed to load doctor routers: {e}")

# ============================================================================
# 5. PATIENT MODULE
# ============================================================================
try:
    from app.api.v1.routers.patient.patient_appointment_booking import router as patient_booking_router
    from app.api.v1.routers.patient.patient_medical_history import router as medical_history_router
    from app.api.v1.routers.patient.patient_document_storage import router as document_storage_router
    from app.api.v1.routers.patient.patient_discharge_summary import router as discharge_summary_router
    from app.api.v1.routers.patient.ipd_management import router as ipd_management_router
    
    api_router.include_router(patient_booking_router)
    api_router.include_router(medical_history_router)
    api_router.include_router(document_storage_router)
    api_router.include_router(discharge_summary_router)
    api_router.include_router(ipd_management_router)
    logger.info("✓ Patient routers loaded")
except ImportError as e:
    logger.error(f"✗ Failed to load patient routers: {e}")

# ============================================================================
# 6. STAFF MANAGEMENT (Nurse & Receptionist)
# ============================================================================
try:
    from app.api.v1.routers.management.nurse_management import router as nurse_management_router
    from app.api.v1.routers.management.receptionist_management import router as receptionist_management_router
    from app.api.v1.routers.management.staff_doctor_schedules import (
        router as staff_doctor_schedules_router,
    )
    from app.api.v1.routers.management.opd_management import (
        router as opd_management_router,
        doctors_router as opd_doctors_router,
    )
    api_router.include_router(nurse_management_router)
    api_router.include_router(receptionist_management_router)
    api_router.include_router(staff_doctor_schedules_router)
    api_router.include_router(opd_management_router)
    api_router.include_router(opd_doctors_router)
    logger.info("✓ Management routers loaded (includes OPD)")
except ImportError as e:
    logger.error(f"✗ Failed to load management routers: {e}")

# ============================================================================
# 7. SURGERY MODULE
# ============================================================================
try:
    from app.api.v1.routers.surgery.routes import router as surgery_router
    api_router.include_router(surgery_router)
    logger.info("✓ Surgery router loaded")
except ImportError as e:
    logger.error(f"✗ Failed to load surgery router: {e}")

# ============================================================================
# 8. PHARMACY MODULE
# ============================================================================
try:
    from app.api.v1.routers.pharmacy.medicines import router as pharmacy_medicines_router
    from app.api.v1.routers.pharmacy.suppliers import router as pharmacy_suppliers_router
    from app.api.v1.routers.pharmacy.purchase_orders import router as pharmacy_purchase_orders_router
    from app.api.v1.routers.pharmacy.grn import router as pharmacy_grn_router
    from app.api.v1.routers.pharmacy.stock import router as pharmacy_stock_router
    from app.api.v1.routers.pharmacy.sales import router as pharmacy_sales_router
    from app.api.v1.routers.pharmacy.returns import router as pharmacy_returns_router
    from app.api.v1.routers.pharmacy.alerts import router as pharmacy_alerts_router
    from app.api.v1.routers.pharmacy.reports import router as pharmacy_reports_router
    
    api_router.include_router(
        pharmacy_medicines_router, prefix="/pharmacy", tags=["Pharmacy - Medicines"], dependencies=_pharmacy_dep
    )
    api_router.include_router(
        pharmacy_suppliers_router, prefix="/pharmacy", tags=["Pharmacy - Suppliers"], dependencies=_pharmacy_dep
    )
    api_router.include_router(
        pharmacy_purchase_orders_router,
        prefix="/pharmacy",
        tags=["Pharmacy - Purchase Orders"],
        dependencies=_pharmacy_dep,
    )
    api_router.include_router(pharmacy_grn_router, prefix="/pharmacy", tags=["Pharmacy - GRN"], dependencies=_pharmacy_dep)
    api_router.include_router(pharmacy_stock_router, prefix="/pharmacy", tags=["Pharmacy - Stock"], dependencies=_pharmacy_dep)
    api_router.include_router(pharmacy_sales_router, prefix="/pharmacy", tags=["Pharmacy - Sales"], dependencies=_pharmacy_dep)
    api_router.include_router(
        pharmacy_returns_router, prefix="/pharmacy", tags=["Pharmacy - Returns"], dependencies=_pharmacy_dep
    )
    api_router.include_router(
        pharmacy_alerts_router, prefix="/pharmacy", tags=["Pharmacy - Alerts"], dependencies=_pharmacy_dep
    )
    api_router.include_router(
        pharmacy_reports_router, prefix="/pharmacy", tags=["Pharmacy - Reports"], dependencies=_pharmacy_dep
    )
    logger.info("✓ Pharmacy routers loaded (9 routers - complete module)")
except ImportError as e:
    logger.error(f"✗ Failed to load pharmacy routers: {e}")

# ============================================================================
# 9. TELEMEDICINE MODULE
# ============================================================================
try:
    from app.api.v1.routers.telemed.tele_appointments import router as telemed_appointments_router
    from app.api.v1.routers.telemed.sessions import router as telemed_sessions_router
    from app.api.v1.routers.telemed.prescriptions import router as telemed_prescriptions_router
    from app.api.v1.routers.telemed.vitals import router as telemed_vitals_router
    from app.api.v1.routers.telemed.notifications import router as telemed_notifications_router
    from app.api.v1.routers.telemed.config import router as telemed_config_router
    api_router.include_router(telemed_appointments_router, prefix="/telemed", dependencies=_telemed_dep)
    api_router.include_router(telemed_sessions_router, prefix="/telemed", dependencies=_telemed_dep)
    api_router.include_router(telemed_prescriptions_router, prefix="/telemed", dependencies=_telemed_dep)
    api_router.include_router(telemed_vitals_router, prefix="/telemed", dependencies=_telemed_dep)
    api_router.include_router(telemed_notifications_router, prefix="/telemed", dependencies=_telemed_dep)
    api_router.include_router(telemed_config_router, prefix="/telemed", dependencies=_telemed_dep)
    logger.info("✓ Telemedicine routers loaded")
except ImportError as e:
    logger.error(f"✗ Failed to load telemedicine routers: {e}")

# ============================================================================
# 10. LAB MODULE
# ============================================================================
try:
    from app.api.v1.routers.lab.lab_equipment import router as lab_equipment_router
    from app.api.v1.routers.lab.lab_tech_dashboard import router as lab_tech_dashboard_router
    from app.api.v1.routers.lab.lab_critical_results import router as lab_critical_results_router
    from app.api.v1.routers.lab.lab_test_registration import router as lab_test_registration_router
    from app.api.v1.routers.lab.lab_sample_tracking import router as lab_sample_tracking_router
    from app.api.v1.routers.lab.lab_report_generation import router as lab_report_generation_router
    from app.api.v1.routers.lab.lab_result_access import router as lab_result_access_router
    from app.api.v1.routers.lab.lab_test_catalogue import router as lab_test_catalogue_router
    from app.api.v1.routers.lab.lab_equipment_tracking import router as lab_equipment_tracking_router
    from app.api.v1.routers.lab.lab_quality_control import router as lab_quality_control_router
    from app.api.v1.routers.lab.lab_profile import router as lab_profile_router

    api_router.include_router(lab_equipment_router, dependencies=_lab_dep)
    api_router.include_router(lab_tech_dashboard_router, dependencies=_lab_dep)
    api_router.include_router(lab_critical_results_router, dependencies=_lab_dep)
    api_router.include_router(lab_test_registration_router, dependencies=_lab_dep)
    api_router.include_router(lab_sample_tracking_router, dependencies=_lab_dep)
    api_router.include_router(lab_report_generation_router, dependencies=_lab_dep)
    api_router.include_router(lab_result_access_router, dependencies=_lab_dep)
    api_router.include_router(lab_test_catalogue_router, dependencies=_lab_dep)
    api_router.include_router(lab_equipment_tracking_router, dependencies=_lab_dep)
    api_router.include_router(lab_quality_control_router, dependencies=_lab_dep)
    api_router.include_router(lab_profile_router, dependencies=_lab_dep)
    logger.info(
        "✓ Lab routers loaded (equipment + tech dashboard + critical results + test registration + sample tracking + report generation + result access + test catalogue + equipment tracking + quality control + profile)"
    )
except ImportError as e:
    logger.error(f"✗ Failed to load lab routers: {e}")

# ============================================================================
# 11. BILLING & ACCOUNTS MODULE
# ============================================================================
try:
    from app.api.v1.routers.billing.services import router as billing_services_router
    from app.api.v1.routers.billing.bills import router as billing_bills_router
    from app.api.v1.routers.insurance.claims import router as insurance_claims_router
    from app.api.v1.routers.reports_finance.reports import router as finance_reports_router
    from app.api.v1.routers.reports_finance.reconciliation import router as finance_reconciliation_router
    from app.api.v1.routers.audit_finance.audit import router as finance_audit_router
    from app.api.v1.routers.billing.documents import router as finance_documents_router

    api_router.include_router(billing_services_router)
    api_router.include_router(billing_bills_router)
    api_router.include_router(finance_documents_router)
    api_router.include_router(insurance_claims_router)
    api_router.include_router(finance_reports_router)
    api_router.include_router(finance_reconciliation_router)
    api_router.include_router(finance_audit_router)
    logger.info("✓ Billing & Accounts routers loaded")
except ImportError as e:
    logger.error(f"✗ Failed to load billing routers: {e}")

# ============================================================================
# 12. NOTIFICATIONS MODULE
# ============================================================================
try:
    from app.api.v1.routers.notifications.notifications import router as notifications_router
    api_router.include_router(notifications_router)
    logger.info("✓ Notifications router loaded (/api/v1/notifications)")
except ImportError as e:
    logger.error("✗ Failed to load notifications router: %s", e)

# ============================================================================
# 13. PAYMENT GATEWAY & WEBHOOKS MODULE
# ============================================================================
try:
    from app.api.v1.routers.payments_gateway.collect import router as payments_gateway_router
    from app.api.v1.routers.payment_webhooks.webhook_receive import router as payment_webhook_receive_router
    from app.api.v1.routers.payment_webhooks.webhook_events import router as payment_webhook_events_router
    api_router.include_router(payments_gateway_router)
    api_router.include_router(payment_webhook_receive_router)
    api_router.include_router(payment_webhook_events_router)
    logger.info("✓ Payment Gateway & Webhooks routers loaded (/api/v1/payments, /api/v1/payments/webhooks)")
except ImportError as e:
    logger.error(f"✗ Failed to load payment gateway routers: {e}")


@api_router.get("/health")
async def health_check():
    """API health check endpoint"""
    from app.schemas.response import SuccessResponse
    return SuccessResponse(
        success=True,
        message="Hospital Management SaaS API is healthy",
        data={
            "status": "healthy",
            "version": "1.0.0",
            "service": "Hospital Management SaaS API",
            "modules": [
                "authentication",
                "superadmin",
                "hospital_admin",
                "doctor",
                "patient",
                "nurse",
                "receptionist",
                "pharmacy",
                "lab",
                "billing",
                "payments",
                "insurance",
                "finance_reports",
                "finance_audit"
            ]
        }
    ).dict()


@api_router.get("/routes")
async def list_routes():
    """List all available API routes for debugging"""
    routes = []
    for route in api_router.routes:
        if hasattr(route, 'path') and hasattr(route, 'methods'):
            routes.append({
                "path": route.path,
                "methods": list(route.methods),
                "name": getattr(route, 'name', 'unnamed')
            })
    
    from app.schemas.response import SuccessResponse
    return SuccessResponse(
        success=True,
        message=f"Found {len(routes)} API routes",
        data={"routes": routes}
    ).dict()
