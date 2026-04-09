"""
Patient management models.
Handles patient profiles, appointments, medical records, and admissions.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, Text, Boolean, DateTime, DECIMAL
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
from app.core.database_types import JSON_TYPE, UUID_TYPE
from app.models.base import TenantBaseModel
from app.core.enums import Gender, BloodGroup, AppointmentStatus, AdmissionType, DocumentType


class PatientProfile(TenantBaseModel):
    """
    Extended profile for patients.
    Links to User model for authentication and basic info.
    """
    __tablename__ = "patient_profiles"
    
    user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False, unique=True)
    
    # Patient identification
    patient_id = Column(String(50), nullable=False)  # Hospital-specific patient ID
    mrn = Column(String(50))  # Medical Record Number
    
    # Personal details
    date_of_birth = Column(String(10), nullable=True)  # YYYY-MM-DD (optional during registration)
    gender = Column(String(10), nullable=True)  # Maps to Gender enum (optional during registration)
    blood_group = Column(String(20))  # A+, B-, OTHER, etc.; use blood_group_value when OTHER
    blood_group_value = Column(String(50))  # Free text when blood_group is OTHER

    # Government / facility ID (OPD registration)
    id_type = Column(String(50))  # e.g. Aadhaar Card, Passport, Other
    id_number = Column(String(100))
    id_name = Column(String(255))  # Label or name on ID when type is Other
    
    # Contact details
    address = Column(Text)
    city = Column(String(100))
    district = Column(String(100))
    state = Column(String(100))
    country = Column(String(100))
    pincode = Column(String(10))
    
    # Emergency contact
    emergency_contact_name = Column(String(100))
    emergency_contact_phone = Column(String(20))
    emergency_contact_relation = Column(String(50))
    
    # Medical information (OPD free-text; structured lists below)
    medical_history = Column(Text)  # Known conditions, allergies, medications narrative

    allergies = Column(JSON_TYPE, nullable=False, default=lambda: [])  # ["penicillin", "peanuts"]
    chronic_conditions = Column(JSON_TYPE, nullable=False, default=lambda: [])  # ["diabetes", "hypertension"]
    current_medications = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Insurance details
    insurance_provider = Column(String(100))
    insurance_policy_number = Column(String(100))
    insurance_expiry = Column(String(10))  # YYYY-MM-DD
    
    # Relationships
    user = relationship("User")
    appointments = relationship("Appointment", back_populates="patient")
    medical_records = relationship("MedicalRecord", back_populates="patient")
    admissions = relationship("Admission", back_populates="patient")
    documents = relationship("PatientDocument", back_populates="patient")
    sales = relationship("Sale", back_populates="patient")
    surgery_cases = relationship("SurgeryCase", back_populates="patient")
    
    def __repr__(self):
        return f"<PatientProfile(id={self.id}, patient_id='{self.patient_id}', hospital_id={self.hospital_id})>"


class Appointment(TenantBaseModel):
    """
    Patient appointments with doctors.
    Supports scheduling, status tracking, and conflict detection.
    """
    __tablename__ = "appointments"
    
    # Public identifier (not the DB UUID)
    appointment_ref = Column(String(20), nullable=False, unique=True)
    
    # Core appointment details
    patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    doctor_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    department_id = Column(UUID_TYPE, ForeignKey("departments.id"), nullable=False)
    
    # Scheduling
    appointment_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    appointment_time = Column(String(8), nullable=False)   # HH:MM:SS
    duration_minutes = Column(Integer, default=30)
    
    # Appointment details
    status = Column(String(20), nullable=False, default=AppointmentStatus.REQUESTED)
    appointment_type = Column(String(50), default="CONSULTATION")  # CONSULTATION, FOLLOW_UP, EMERGENCY
    chief_complaint = Column(Text)
    notes = Column(Text)
    
    # Tracking
    checked_in_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    cancelled_at = Column(DateTime(timezone=True))
    cancellation_reason = Column(Text)
    
    # Billing
    consultation_fee = Column(DECIMAL(10, 2))
    is_paid = Column(Boolean, default=False)
    
    # Audit fields
    created_by_role = Column(String(20), nullable=False)  # PATIENT, DOCTOR, HOSPITAL_ADMIN
    created_by_user = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    
    # Relationships
    patient = relationship("PatientProfile", back_populates="appointments")
    doctor = relationship("User", foreign_keys=[doctor_id])
    department = relationship("Department", back_populates="appointments")
    medical_record = relationship("MedicalRecord", back_populates="appointment", uselist=False)
    creator = relationship("User", foreign_keys=[created_by_user])
    
    def __repr__(self):
        return f"<Appointment(ref='{self.appointment_ref}', date='{self.appointment_date}', status='{self.status}')>"


class MedicalRecord(TenantBaseModel):
    """
    Patient medical records created during appointments.
    Immutable records for compliance and audit purposes.
    """
    __tablename__ = "medical_records"
    
    # Links
    patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    doctor_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=True)  # Nullable for nurse entries
    appointment_id = Column(UUID_TYPE, ForeignKey("appointments.id"))
    
    # Medical details
    chief_complaint = Column(Text, nullable=False)
    history_of_present_illness = Column(Text)
    past_medical_history = Column(Text)
    examination_findings = Column(Text)
    
    # Vital signs
    vital_signs = Column(JSON_TYPE, nullable=False, default=lambda: {})  # {"bp": "120/80", "pulse": 72, "temp": 98.6}
    
    # Assessment and plan
    diagnosis = Column(Text)
    differential_diagnosis = Column(JSON_TYPE, nullable=False, default=lambda: [])
    treatment_plan = Column(Text)
    follow_up_instructions = Column(Text)
    
    # Prescriptions and orders
    prescriptions = Column(JSON_TYPE, nullable=False, default=lambda: [])
    lab_orders = Column(JSON_TYPE, nullable=False, default=lambda: [])
    imaging_orders = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Record metadata
    is_finalized = Column(Boolean, default=False)
    finalized_at = Column(DateTime(timezone=True))
    
    # Relationships
    patient = relationship("PatientProfile", back_populates="medical_records")
    # Note: doctor now links to users.id, not doctor_profiles.id
    doctor = relationship("User", foreign_keys=[doctor_id])
    appointment = relationship("Appointment", back_populates="medical_record")
    
    def __repr__(self):
        return f"<MedicalRecord(id={self.id}, patient_id={self.patient_id}, doctor_id={self.doctor_id})>"

class Admission(TenantBaseModel):
    """
    Patient admissions for OPD/IPD management.
    Tracks patient stay and discharge process.
    """
    __tablename__ = "admissions"
    
    # Core details
    patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    doctor_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    department_id = Column(UUID_TYPE, ForeignKey("departments.id"), nullable=False)
    
    # Admission details
    admission_number = Column(String(50), nullable=False, unique=True)
    admission_type = Column(String(10), nullable=False)  # Maps to AdmissionType enum
    admission_date = Column(DateTime(timezone=True), nullable=False)
    
    # Clinical details
    chief_complaint = Column(Text, nullable=False)
    provisional_diagnosis = Column(Text)
    admission_notes = Column(Text)
    
    # Bed assignment (for IPD)
    bed_id = Column(UUID_TYPE, ForeignKey("beds.id"), nullable=True)
    ward = Column(String(100))
    room_number = Column(String(20))
    bed_number = Column(String(20))
    
    # Discharge details
    discharge_date = Column(DateTime(timezone=True))
    discharge_type = Column(String(50))  # NORMAL, LAMA, DEATH, TRANSFER
    discharge_summary_id = Column(UUID_TYPE, ForeignKey("discharge_summaries.id"))
    
    # Status
    is_active = Column(Boolean, default=True)
    
    # Relationships
    patient = relationship("PatientProfile", back_populates="admissions")
    # Note: doctor now links to users.id, not doctor_profiles.id
    doctor = relationship("User", foreign_keys=[doctor_id])
    department = relationship("Department")
    bed = relationship("Bed", foreign_keys=[bed_id], lazy="select")
    discharge_summary = relationship("DischargeSummary", back_populates="admission")
    surgery_cases = relationship("SurgeryCase", back_populates="admission")
    
    @hybrid_property
    def status(self):
        """Derived status: PENDING (no bed), ADMITTED (has bed), or DISCHARGED."""
        if not self.is_active or self.discharge_date is not None:
            return "DISCHARGED"
        if getattr(self, "bed_id", None) is not None:
            return "ADMITTED"
        return "PENDING"
    
    def __repr__(self):
        return f"<Admission(id={self.id}, number='{self.admission_number}', type='{self.admission_type}')>"


class DischargeSummary(TenantBaseModel):
    """
    Comprehensive discharge summary for admitted patients.
    Critical document for continuity of care.
    """
    __tablename__ = "discharge_summaries"
    
    # Links
    patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    doctor_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    
    # Summary details
    admission_date = Column(DateTime(timezone=True), nullable=False)
    discharge_date = Column(DateTime(timezone=True), nullable=False)
    length_of_stay = Column(Integer)  # Days
    
    # Clinical summary
    chief_complaint = Column(Text, nullable=False)
    final_diagnosis = Column(Text, nullable=False)
    secondary_diagnoses = Column(JSON_TYPE, nullable=False, default=lambda: [])
    procedures_performed = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Treatment summary
    hospital_course = Column(Text)
    medications_on_discharge = Column(JSON_TYPE, nullable=False, default=lambda: [])
    follow_up_instructions = Column(Text)
    diet_instructions = Column(Text)
    activity_restrictions = Column(Text)
    
    # Follow-up
    follow_up_date = Column(String(10))  # YYYY-MM-DD
    follow_up_doctor = Column(String(100))
    
    # Document status
    is_finalized = Column(Boolean, default=False)
    finalized_at = Column(DateTime(timezone=True))
    
    # Relationships
    patient = relationship("PatientProfile")
    # Note: doctor now links to users.id, not doctor_profiles.id
    doctor = relationship("User", foreign_keys=[doctor_id])
    # One-to-one: the single Admission that references this summary (via admission.discharge_summary_id)
    admission = relationship("Admission", back_populates="discharge_summary", uselist=False)
    
    def __repr__(self):
        return f"<DischargeSummary(id={self.id}, patient_id={self.patient_id})>"


class PatientDocument(TenantBaseModel):
    """
    Patient documents and attachments.
    Supports various document types with secure storage.
    """
    __tablename__ = "patient_documents"
    
    # Links
    patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    uploaded_by = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    
    # Document details
    document_type = Column(String(50), nullable=False)  # Maps to DocumentType enum
    title = Column(String(255), nullable=False)
    description = Column(Text)
    
    # File details
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)  # Bytes
    mime_type = Column(String(100))
    
    # Document metadata
    document_date = Column(String(10))  # YYYY-MM-DD (when document was created/issued)
    is_sensitive = Column(Boolean, default=True)  # PHI/PII flag
    
    # Relationships
    patient = relationship("PatientProfile", back_populates="documents")
    uploader = relationship("User")
    
    def __repr__(self):
        return f"<PatientDocument(id={self.id}, type='{self.document_type}', title='{self.title}')>"