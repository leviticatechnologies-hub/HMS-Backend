"""
Platform and tenant models for multi-tenant SaaS architecture.
These models manage hospital onboarding and subscription lifecycle.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean, DECIMAL
from sqlalchemy.orm import relationship
from app.models.base import BaseModel
from app.core.enums import SubscriptionPlan, SubscriptionStatus
from app.core.database_types import JSON_TYPE, UUID_TYPE


class Hospital(BaseModel):
    """
    Core tenant model - represents one hospital in the SaaS platform.
    This is the root of multi-tenant isolation.
    """
    __tablename__ = "hospitals"
    
    name = Column(String(255), nullable=False)
    registration_number = Column(String(100), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20), nullable=False)
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    country = Column(String(100), nullable=False)
    pincode = Column(String(10), nullable=False)
    
    # Hospital metadata
    license_number = Column(String(100))
    established_date = Column(DateTime(timezone=True))
    website = Column(String(255))
    logo_url = Column(String(500))
    
    # Hospital status
    is_active = Column(Boolean, default=True, nullable=False)
    status = Column(String(20), default="ACTIVE", nullable=False)  # ACTIVE, SUSPENDED, INACTIVE

    # Dedicated PostgreSQL database on the same server (platform/registry DB stores this name only)
    tenant_database_name = Column(String(63), unique=True, nullable=True, index=True)
    
    # Configuration settings
    settings = Column(JSON_TYPE, nullable=False, default=lambda: {})
    
    # Relationships
    subscription = relationship("HospitalSubscription", back_populates="hospital", uselist=False)
    users = relationship("User")
    departments = relationship("Department", back_populates="hospital")
    wards = relationship("Ward", back_populates="hospital")
    # Pharmacy relationships
    medicines = relationship("Medicine", foreign_keys="Medicine.hospital_id", back_populates="hospital")
    stock_batches = relationship("StockBatch", foreign_keys="StockBatch.hospital_id", back_populates="hospital")
    suppliers = relationship("Supplier", foreign_keys="Supplier.hospital_id", back_populates="hospital")
    purchase_orders = relationship("PurchaseOrder", foreign_keys="PurchaseOrder.hospital_id", back_populates="hospital")
    sales = relationship("Sale", foreign_keys="Sale.hospital_id", back_populates="hospital")
    # Lab relationships (no back_populates - models inherit from TenantBaseModel)
    lab_test_categories = relationship("LabTestCategory", back_populates="hospital")
    lab_tests = relationship("LabTest", back_populates="hospital")
    lab_orders = relationship("LabOrder", back_populates="hospital")
    lab_samples = relationship("Sample", back_populates="hospital")
    test_results = relationship("TestResult", back_populates="hospital")
    lab_reports = relationship("LabReport", back_populates="hospital")
    lab_equipment = relationship("Equipment", back_populates="hospital")
    qc_rules = relationship("QCRule", back_populates="hospital")
    qc_runs = relationship("QCRun", back_populates="hospital")
    report_share_tokens = relationship("ReportShareToken", back_populates="hospital")
    notifications = relationship("NotificationOutbox", back_populates="hospital")
    report_access_logs = relationship("ReportAccess", back_populates="hospital")
    lab_audit_logs = relationship("LabAuditLog", back_populates="hospital")
    chain_of_custody = relationship("ChainOfCustody", back_populates="hospital")
    compliance_exports = relationship("ComplianceExport", back_populates="hospital")
    
    def __repr__(self):
        return f"<Hospital(id={self.id}, name='{self.name}')>"


class SubscriptionPlanModel(BaseModel):
    """
    Subscription plans available in the platform (Free/Standard/Premium).
    Defines features and limits for each tier.
    """
    __tablename__ = "subscription_plans"
    
    name = Column(String(50), nullable=False)  # Maps to SubscriptionPlan enum
    display_name = Column(String(100), nullable=False)
    description = Column(Text)
    
    # Pricing
    monthly_price = Column(DECIMAL(10, 2), nullable=False, default=0)
    yearly_price = Column(DECIMAL(10, 2), nullable=False, default=0)
    
    # Feature limits
    max_doctors = Column(Integer, default=0)  # 0 = unlimited
    max_patients = Column(Integer, default=0)  # 0 = unlimited
    max_appointments_per_month = Column(Integer, default=0)
    max_storage_gb = Column(Integer, default=1)
    
    # Feature flags
    features = Column(JSON_TYPE, nullable=False, default=lambda: {})  # {"telemedicine": true, "reports": true}
    
    def __repr__(self):
        return f"<SubscriptionPlan(name='{self.name}', price=${self.monthly_price})>"


class HospitalSubscription(BaseModel):
    """
    Maps hospitals to their subscription plans.
    Tracks subscription lifecycle and billing.
    """
    __tablename__ = "hospital_subscriptions"
    
    hospital_id = Column(UUID_TYPE, ForeignKey("hospitals.id"), nullable=False, unique=True)
    plan_id = Column(UUID_TYPE, ForeignKey("subscription_plans.id"), nullable=False)
    
    # Subscription lifecycle
    status = Column(String(20), nullable=False, default=SubscriptionStatus.ACTIVE)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    
    # Billing
    is_trial = Column(Boolean, default=False)
    trial_end_date = Column(DateTime(timezone=True))
    auto_renew = Column(Boolean, default=True)
    
    # Usage tracking
    current_usage = Column(JSON_TYPE, nullable=False, default=lambda: {})  # {"doctors": 5, "patients": 150}
    
    # Relationships
    hospital = relationship("Hospital", back_populates="subscription")
    plan = relationship("SubscriptionPlanModel")
    
    def __repr__(self):
        return f"<HospitalSubscription(hospital_id={self.hospital_id}, status='{self.status}')>"