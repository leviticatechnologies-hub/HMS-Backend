"""
Digital prescription models for telemedicine consultations.
Handles prescription creation, PDF generation, and pharmacy/lab integration.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Integer, Text, JSON
from app.core.database_types import UUID_TYPE
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import TenantBaseModel
from app.core.enums import (
    PrescriptionStatus, IntegrationType, IntegrationStatus, TestUrgency
)


class TelePrescription(TenantBaseModel):
    """
    Digital prescription for telemedicine consultations.
    Links to tele-appointment and manages prescription lifecycle.
    """
    __tablename__ = "tele_prescriptions"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    
    # Link to tele-appointment (nullable for safety; FK optional for existing/migrating data)
    tele_appointment_id = Column(UUID_TYPE, ForeignKey("tele_appointments.id"), nullable=True, unique=True, index=True)
    # Link to telemed session (nullable; for session-scoped prescriptions)
    session_id = Column(UUID_TYPE, ForeignKey("telemed_sessions.id"), nullable=True, index=True)
    
    # Prescription identification
    prescription_no = Column(String(50), nullable=False, unique=True, index=True)
    
    # Core references
    doctor_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False, index=True)
    patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False, index=True)
    
    # Clinical information
    diagnosis = Column(Text, nullable=False)
    clinical_notes = Column(Text, nullable=True)
    follow_up_date = Column(String(10), nullable=True)  # YYYY-MM-DD
    
    # Status management
    status = Column(String(20), nullable=False, default=PrescriptionStatus.DRAFT, index=True)
    signed_at = Column(DateTime(timezone=True), nullable=True)
    signed_by = Column(UUID_TYPE, ForeignKey("users.id"), nullable=True)
    signature_data = Column(Text, nullable=True)  # Digital signature metadata
    
    # Cancellation
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by = Column(UUID_TYPE, ForeignKey("users.id"), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    tele_appointment = relationship("TeleAppointment")
    telemed_session = relationship("TelemedSession", foreign_keys=[session_id])
    doctor = relationship("User", foreign_keys=[doctor_id])
    patient = relationship("PatientProfile")
    signed_by_user = relationship("User", foreign_keys=[signed_by])
    cancelled_by_user = relationship("User", foreign_keys=[cancelled_by])
    medicines = relationship("PrescriptionMedicine", back_populates="prescription", cascade="all, delete-orphan")
    lab_orders = relationship("PrescriptionLabOrder", back_populates="prescription", cascade="all, delete-orphan")
    pdfs = relationship("PrescriptionPDF", back_populates="prescription", cascade="all, delete-orphan")
    integrations = relationship("PrescriptionIntegration", back_populates="prescription", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<TelePrescription(id={self.id}, prescription_no='{self.prescription_no}', status='{self.status}')>"


class PrescriptionMedicine(TenantBaseModel):
    """
    Medicine items in a prescription with dosage instructions.
    """
    __tablename__ = "prescription_medicines"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    
    # Link to prescription
    prescription_id = Column(UUID_TYPE, ForeignKey("tele_prescriptions.id"), nullable=False, index=True)
    
    # Medicine information
    medicine_id = Column(UUID_TYPE, ForeignKey("pharmacy_medicines.id"), nullable=True)  # Optional FK
    medicine_name = Column(String(200), nullable=False)  # Always store for immutability
    medicine_strength = Column(String(50), nullable=True)
    medicine_form = Column(String(50), nullable=True)  # TABLET, CAPSULE, SYRUP, etc.
    
    # Dosage instructions
    dose = Column(String(50), nullable=False)  # "500mg", "1 tablet", "5ml"
    frequency = Column(String(50), nullable=False)  # "1-0-1", "twice daily", "every 6 hours"
    duration_days = Column(Integer, nullable=False)
    instructions = Column(Text, nullable=True)  # "After food", "Before sleep", etc.
    
    # Quantity
    quantity = Column(Integer, nullable=True)  # Total quantity to dispense
    quantity_unit = Column(String(20), nullable=True)  # "tablets", "bottles", "vials"
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    prescription = relationship("TelePrescription", back_populates="medicines")
    medicine = relationship("Medicine", foreign_keys=[medicine_id])
    
    def __repr__(self):
        return f"<PrescriptionMedicine(id={self.id}, medicine_name='{self.medicine_name}')>"


class PrescriptionLabOrder(TenantBaseModel):
    """
    Lab test orders in a prescription.
    """
    __tablename__ = "prescription_lab_orders"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    
    # Link to prescription
    prescription_id = Column(UUID_TYPE, ForeignKey("tele_prescriptions.id"), nullable=False, index=True)
    
    # Optional reference to a catalogue test id (no FK; lab module may be minimal/rebuilt)
    lab_test_id = Column(UUID_TYPE, nullable=True, index=True)
    test_name = Column(String(200), nullable=False)  # Always store for immutability
    test_code = Column(String(50), nullable=True)
    test_category = Column(String(100), nullable=True)
    
    # Instructions
    clinical_notes = Column(Text, nullable=True)
    urgency = Column(String(20), nullable=False, default=TestUrgency.ROUTINE)
    
    # Integration status
    sent_to_lab = Column(Boolean, nullable=False, default=False)
    lab_order_id = Column(UUID_TYPE, nullable=True)  # Reference to created lab order
    sent_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    prescription = relationship("TelePrescription", back_populates="lab_orders")
    
    def __repr__(self):
        return f"<PrescriptionLabOrder(id={self.id}, test_name='{self.test_name}')>"


class PrescriptionPDF(TenantBaseModel):
    """
    PDF files generated for prescriptions.
    """
    __tablename__ = "prescription_pdfs"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    
    # Link to prescription
    prescription_id = Column(UUID_TYPE, ForeignKey("tele_prescriptions.id"), nullable=False, index=True)
    
    # PDF metadata
    file_name = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)  # Storage path or URL
    file_size = Column(Integer, nullable=True)
    mime_type = Column(String(50), nullable=False, default='application/pdf')
    
    # Generation info
    generated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    generated_by = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    template_version = Column(String(20), nullable=True)
    
    # Access control
    access_token = Column(String(100), nullable=True, unique=True)  # For secure PDF access
    expires_at = Column(DateTime(timezone=True), nullable=True)
    download_count = Column(Integer, nullable=False, default=0)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    prescription = relationship("TelePrescription", back_populates="pdfs")
    generated_by_user = relationship("User")
    
    def __repr__(self):
        return f"<PrescriptionPDF(id={self.id}, file_name='{self.file_name}')>"


class PrescriptionIntegration(TenantBaseModel):
    """
    Integration status tracking for pharmacy and lab orders.
    """
    __tablename__ = "prescription_integrations"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    
    # Link to prescription
    prescription_id = Column(UUID_TYPE, ForeignKey("tele_prescriptions.id"), nullable=False, index=True)
    
    # Integration details
    integration_type = Column(String(20), nullable=False, index=True)  # IntegrationType enum
    target_module = Column(String(50), nullable=True)  # Target system identifier
    
    # Status tracking
    status = Column(String(20), nullable=False, default=IntegrationStatus.PENDING, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    
    # Integration data
    external_reference = Column(String(100), nullable=True)  # Reference in target system
    request_payload = Column(JSON, nullable=True)
    response_payload = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # Retry logic
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    prescription = relationship("TelePrescription", back_populates="integrations")
    
    def __repr__(self):
        return f"<PrescriptionIntegration(id={self.id}, type='{self.integration_type}', status='{self.status}')>"