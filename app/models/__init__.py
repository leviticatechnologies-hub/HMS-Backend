"""
Models package initialization.
Imports all models to ensure proper SQLAlchemy relationship resolution.
"""

# Import base models first
from app.models.base import BaseModel, TenantBaseModel

# Import core models
from app.models.tenant import Hospital, SubscriptionPlanModel, HospitalSubscription
from app.models.user import User, Role, Permission, user_roles, role_permissions, AuditLog

# Import hospital administration models
from app.models.hospital import Department, StaffProfile, Ward, Bed

# Import doctor models
from app.models.doctor import DoctorProfile, Prescription, PrescriptionNotification, TreatmentPlan

# Import nurse and receptionist models
from app.models.nurse import NurseProfile
from app.models.receptionist import ReceptionistProfile

# Import schedule models
from app.models.schedule import DoctorSchedule

# Import patient models
from app.models.patient import PatientProfile, Appointment, MedicalRecord, PatientDocument, Admission, DischargeSummary

# OPD queue / consultation (outpatient visits)
from app.models.opd_management import (
    OpdVisit,
    OpdConsultation,
    OpdVitalSign,
    OpdTokenLog,
    OpdPatientTransfer,
)

# Import surgery models (after patient for relationship resolution)
from app.models.surgery import (
    SurgeryCase,
    SurgeryTeamMember,
    SurgeryDocumentation,
    SurgeryVideo,
    SurgeryVideoViewAudit,
)

# Import pharmacy models
from app.models.pharmacy import (
    Medicine, Supplier, PurchaseOrder, PurchaseOrderItem,
    GoodsReceipt, GoodsReceiptItem, StockBatch, StockLedger,
    Sale, SaleItem, Return, ReturnItem, ExpiryAlert
)

# Lab (minimal: equipment + maintenance)
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

# Import prescription models
from app.models.prescription import TelePrescription, PrescriptionMedicine, PrescriptionLabOrder, PrescriptionPDF, PrescriptionIntegration

# Import telemedicine models
from app.models.telemedicine import (
    TeleAppointment,
    TelemedSession,
    TelemedParticipant,
    TelemedMessage,
    TelemedFile,
    TelemedConsultationNote,
    TelemedVitals,
    TelemedNotification,
    TelemedProviderConfig,
)

# Import password history model
from app.models.password_history import PasswordHistory

# Import billing & accounts models
from app.models.billing import (
    ServiceItem, TaxProfile, Bill, BillItem, IPDCharge,
    BillingPayment, FinancialDocument, InsuranceClaim, Reconciliation, FinanceAuditLog,
)

# Import support models
from app.models.support import SupportTicket

# Public demo requests (marketing / DCM)
from app.models.demo_request import DemoRequest
from app.models.contact_message import ContactMessage

__all__ = [
    # Base models
    "BaseModel",
    "TenantBaseModel",
    
    # Core models
    "Hospital",
    "SubscriptionPlanModel", 
    "HospitalSubscription",
    "User",
    "Role",
    "Permission",
    "user_roles",
    "role_permissions",
    "AuditLog",
    
    # Hospital administration
    "Department",
    "StaffProfile",
    "Ward",
    "Bed",
    
    # Doctor models
    "DoctorProfile",
    "DoctorSchedule",
    "Prescription",
    "TreatmentPlan",
    
    # Nurse and receptionist models
    "NurseProfile",
    "ReceptionistProfile",
    
    # Patient models
    "PatientProfile",
    "Appointment",
    "MedicalRecord",
    "PatientDocument",
    "Admission",
    "DischargeSummary",
    "OpdVisit",
    "OpdConsultation",
    "OpdVitalSign",
    "OpdTokenLog",
    "OpdPatientTransfer",
    
    # Surgery models
    "SurgeryCase",
    "SurgeryTeamMember",
    "SurgeryDocumentation",
    "SurgeryVideo",
    "SurgeryVideoViewAudit",
    
    # Pharmacy models
    "Medicine",
    "Supplier",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "GoodsReceipt",
    "GoodsReceiptItem",
    "StockBatch",
    "StockLedger",
    "Sale",
    "SaleItem",
    "Return",
    "ReturnItem",
    "ExpiryAlert",
    
    # Lab
    "Equipment",
    "EquipmentMaintenanceLog",
    "LabTestRegistration",
    "LabCriticalAlert",
    "LabSampleTracking",
    "LabReportRecord",
    "LabReportReadyTest",
    "LabResultAccessGrant",
    "LabResultAccessLog",
    "LabTestCategory",
    "LabCatalogueTest",
    "LabQcRun",
    "LabQcMaterial",
    "LabQcRule",
    "LabProfileConfig",
    
    # Prescription models
    "TelePrescription",
    "PrescriptionMedicine", 
    "PrescriptionLabOrder",
    "PrescriptionPDF",
    "PrescriptionIntegration",
    
    # Telemedicine models
    "TeleAppointment",
    "TelemedSession",
    "TelemedParticipant",
    "TelemedMessage",
    "TelemedFile",
    "TelemedConsultationNote",
    "TelemedVitals",
    "TelemedNotification",
    "TelemedProviderConfig",

    # Password history
    "PasswordHistory",
    # Billing & accounts
    "ServiceItem",
    "TaxProfile",
    "Bill",
    "BillItem",
    "IPDCharge",
    "BillingPayment",
    "FinancialDocument",
    "InsuranceClaim",
    "Reconciliation",
    "FinanceAuditLog",

    # Support
    "SupportTicket",

    # Demo / DCM
    "DemoRequest",
    "ContactMessage",
]
