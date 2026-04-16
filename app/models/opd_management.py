"""
OPD (outpatient) queue: visits, consultations, vitals, token log, transfers.
Maps spec tables opd_patient → opd_visits, consultation, vital_signs, token_log, patient_transfer_log.
"""
from sqlalchemy import Column, String, Integer, Text, DateTime, Date, ForeignKey, DECIMAL, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import TenantBaseModel
from app.core.database_types import JSON_TYPE, UUID_TYPE


class OpdVisit(TenantBaseModel):
    """One OPD visit / token row (spec: opd_patient)."""

    __tablename__ = "opd_visits"
    __table_args__ = (
        UniqueConstraint("hospital_id", "opd_ref", name="uq_opd_visits_hospital_opd_ref"),
    )

    opd_ref = Column(String(40), nullable=False, index=True)
    patient_profile_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False, index=True)

    patient_name = Column(String(255), nullable=False)
    age = Column(Integer, nullable=True)
    gender = Column(String(20), nullable=True)
    phone_no = Column(String(30), nullable=True)
    blood_group = Column(String(20), nullable=True)

    token_no = Column(String(40), nullable=False, index=True)
    visit_type = Column(String(30), nullable=False, default="NEW")
    priority = Column(String(20), nullable=False, default="NORMAL")
    department_name = Column(String(200), nullable=True)
    department_id = Column(UUID_TYPE, ForeignKey("departments.id"), nullable=True)

    doctor_user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=True, index=True)

    status = Column(String(30), nullable=False, default="WAITING", index=True)
    queue_position = Column(Integer, nullable=False, default=0)
    waiting_time = Column(Integer, nullable=True)
    arrival_time = Column(DateTime(timezone=True), nullable=True)

    appointment_id = Column(UUID_TYPE, ForeignKey("appointments.id"), nullable=True)

    patient = relationship("PatientProfile", foreign_keys=[patient_profile_id])
    doctor = relationship("User", foreign_keys=[doctor_user_id])
    department = relationship("Department", foreign_keys=[department_id])
    appointment = relationship("Appointment", foreign_keys=[appointment_id])
    consultation = relationship(
        "OpdConsultation",
        back_populates="opd_visit",
        uselist=False,
    )


class OpdConsultation(TenantBaseModel):
    """Clinical consultation tied to one OPD visit."""

    __tablename__ = "opd_consultations"

    opd_visit_id = Column(UUID_TYPE, ForeignKey("opd_visits.id"), nullable=False, unique=True, index=True)
    patient_profile_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    doctor_user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)

    consultation_type = Column(String(30), nullable=False, default="NEW")
    symptoms = Column(Text, nullable=True)
    diagnosis = Column(Text, nullable=True)
    prescription = Column(Text, nullable=True)
    tests_recommended = Column(JSON_TYPE, nullable=True, default=lambda: [])
    remarks = Column(Text, nullable=True)
    next_visit_date = Column(Date, nullable=True)

    medical_record_id = Column(UUID_TYPE, ForeignKey("medical_records.id"), nullable=True)

    opd_visit = relationship("OpdVisit", back_populates="consultation")
    vitals = relationship("OpdVitalSign", back_populates="consultation", uselist=False)


class OpdVitalSign(TenantBaseModel):
    """Vitals for one consultation (spec: vital_signs)."""

    __tablename__ = "opd_vital_signs"

    consultation_id = Column(UUID_TYPE, ForeignKey("opd_consultations.id"), nullable=False, unique=True)

    bp = Column(String(30), nullable=True)
    pulse = Column(Integer, nullable=True)
    temperature = Column(DECIMAL(5, 2), nullable=True)
    spo2 = Column(Integer, nullable=True)
    weight = Column(DECIMAL(8, 2), nullable=True)
    height = Column(DECIMAL(8, 2), nullable=True)

    consultation = relationship("OpdConsultation", back_populates="vitals")


class OpdTokenLog(TenantBaseModel):
    """Audit log for token lifecycle."""

    __tablename__ = "opd_token_logs"

    token_no = Column(String(40), nullable=False, index=True)
    patient_profile_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    doctor_user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=True)
    generated_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(30), nullable=False, default="ACTIVE")
    opd_visit_id = Column(UUID_TYPE, ForeignKey("opd_visits.id"), nullable=True)


class OpdPatientTransfer(TenantBaseModel):
    """Doctor reassignment for an OPD visit."""

    __tablename__ = "opd_patient_transfers"

    opd_visit_id = Column(UUID_TYPE, ForeignKey("opd_visits.id"), nullable=False, index=True)
    patient_profile_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)

    from_doctor_user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=True)
    to_doctor_user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    reason = Column(String(500), nullable=True)
    transferred_at = Column(DateTime(timezone=True), nullable=False)
