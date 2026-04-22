"""
Lab portal tables for dashboard modules beyond equipment/maintenance.
"""
import uuid

from sqlalchemy import Column, Date, DECIMAL, Integer, String, Text

from app.core.database_types import UUID_TYPE
from app.models.base import TenantBaseModel


class LabTestRegistration(TenantBaseModel):
    __tablename__ = "lab_test_registrations"

    test_id = Column(String(50), nullable=False, unique=True, index=True)
    patient_ref = Column(String(80), nullable=True, index=True)
    patient_name = Column(String(120), nullable=False)
    doctor_name = Column(String(120), nullable=True)
    test_type = Column(String(120), nullable=False)
    sample_type = Column(String(40), nullable=False)
    priority = Column(String(20), nullable=False, default="ROUTINE")
    status = Column(String(30), nullable=False, default="SAMPLE_PENDING")
    special_instructions = Column(Text, nullable=True)
    registered_date = Column(Date, nullable=False)


class LabCriticalAlert(TenantBaseModel):
    __tablename__ = "lab_critical_alerts"

    alert_id = Column(String(60), nullable=False, unique=True, index=True)
    test_id = Column(String(60), nullable=False, index=True)
    patient_ref = Column(String(80), nullable=True, index=True)
    patient_name = Column(String(120), nullable=False)
    doctor_name = Column(String(120), nullable=True)
    test_name = Column(String(120), nullable=False)
    result_value = Column(String(80), nullable=False)
    alert_level = Column(String(20), nullable=False)
    result_time_label = Column(String(30), nullable=False)
    notify_status = Column(String(20), nullable=False, default="PENDING")
    acknowledged = Column(String(5), nullable=False, default="false")


class LabSampleTracking(TenantBaseModel):
    __tablename__ = "lab_sample_tracking"

    barcode = Column(String(60), nullable=False, unique=True, index=True)
    test_id = Column(String(60), nullable=False, index=True)
    patient_ref = Column(String(80), nullable=True, index=True)
    patient_name = Column(String(120), nullable=False)
    doctor_name = Column(String(120), nullable=True)
    test_type = Column(String(120), nullable=False)
    sample_type = Column(String(40), nullable=False)
    collection_time = Column(String(40), nullable=False)
    status = Column(String(30), nullable=False)
    current_location = Column(String(160), nullable=False)


class LabReportRecord(TenantBaseModel):
    __tablename__ = "lab_report_records"

    report_id = Column(String(60), nullable=False, unique=True, index=True)
    patient_ref = Column(String(80), nullable=True, index=True)
    patient_name = Column(String(120), nullable=False)
    doctor_name = Column(String(120), nullable=True)
    test_type = Column(String(120), nullable=False)
    completion_date = Column(Date, nullable=False)
    status = Column(String(30), nullable=False, default="DRAFT")
    verified_by = Column(String(120), nullable=True)
    template = Column(String(40), nullable=False, default="STANDARD")


class LabReportReadyTest(TenantBaseModel):
    __tablename__ = "lab_report_ready_tests"

    source_test_id = Column(String(60), nullable=False, unique=True, index=True)
    patient_ref = Column(String(80), nullable=True, index=True)
    patient_name = Column(String(120), nullable=False)
    doctor_name = Column(String(120), nullable=True)
    test_type = Column(String(120), nullable=False)
    completed_on = Column(Date, nullable=False)


class LabResultAccessGrant(TenantBaseModel):
    __tablename__ = "lab_result_access_grants"

    grant_id = Column(UUID_TYPE, nullable=False, unique=True, default=uuid.uuid4, index=True)
    patient_ref = Column(String(80), nullable=False, index=True)
    patient_name = Column(String(120), nullable=False)
    doctor_name = Column(String(120), nullable=True)
    email = Column(String(255), nullable=False)
    phone = Column(String(30), nullable=True)
    access_type = Column(String(20), nullable=False, default="VIEW_ONLY")
    status = Column(String(20), nullable=False, default="ACTIVE")
    access_count = Column(Integer, nullable=False, default=0)
    access_code = Column(String(40), nullable=False)
    expiry_date = Column(String(20), nullable=True)
    last_access = Column(String(40), nullable=True)


class LabResultAccessLog(TenantBaseModel):
    __tablename__ = "lab_result_access_logs"

    patient_ref = Column(String(80), nullable=True, index=True)
    patient_name = Column(String(120), nullable=False)
    accessed_by = Column(String(255), nullable=False)
    doctor_name = Column(String(120), nullable=True)
    access_time = Column(String(40), nullable=False)
    action = Column(String(60), nullable=False)
    ip_address = Column(String(64), nullable=False)
    device_browser = Column(String(120), nullable=False)


class LabTestCategory(TenantBaseModel):
    __tablename__ = "lab_test_categories"

    category_name = Column(String(120), nullable=False, unique=True, index=True)


class LabCatalogueTest(TenantBaseModel):
    __tablename__ = "lab_catalogue_tests"

    test_code = Column(String(40), nullable=False, unique=True, index=True)
    test_name = Column(String(160), nullable=False)
    category = Column(String(120), nullable=False, index=True)
    sample_type = Column(String(60), nullable=False)
    turnaround_time = Column(String(60), nullable=False)
    price_inr = Column(DECIMAL(10, 2), nullable=False, default=0)
    parameters_count = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="ACTIVE")
    test_instructions = Column(Text, nullable=True)


class LabQcRun(TenantBaseModel):
    __tablename__ = "lab_qc_runs"

    qc_id = Column(String(60), nullable=False, unique=True, index=True)
    test = Column(String(120), nullable=False)
    qc_material = Column(String(120), nullable=False)
    lot_number = Column(String(80), nullable=False)
    run_date = Column(String(20), nullable=False)
    operator = Column(String(120), nullable=False)
    status = Column(String(20), nullable=False)
    observed_value = Column(DECIMAL(10, 3), nullable=False)


class LabQcMaterial(TenantBaseModel):
    __tablename__ = "lab_qc_materials"

    material_name = Column(String(160), nullable=False)
    material_type = Column(String(80), nullable=False)
    manufacturer = Column(String(120), nullable=False)
    lot_number = Column(String(80), nullable=False)
    expiry_date = Column(String(20), nullable=False)
    storage = Column(String(40), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)


class LabQcRule(TenantBaseModel):
    __tablename__ = "lab_qc_rules"

    rule_name = Column(String(120), nullable=False)
    description = Column(Text, nullable=False)
    rule_type = Column(String(80), nullable=False)
    action_required = Column(String(160), nullable=False)
    priority = Column(String(20), nullable=False, default="MEDIUM")


class LabProfileConfig(TenantBaseModel):
    __tablename__ = "lab_profile_configs"

    lab_id = Column(String(40), nullable=False, default="LAB-001")
    lab_name = Column(String(180), nullable=False)
    lab_type = Column(String(120), nullable=False)
    registration_number = Column(String(80), nullable=False)
    established_date = Column(String(20), nullable=False)
    accreditation = Column(String(120), nullable=False)
    accreditation_number = Column(String(120), nullable=True)
    address = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(20), nullable=True)
    phone = Column(String(30), nullable=True)
    emergency_phone = Column(String(30), nullable=True)
    email = Column(String(255), nullable=True)
    website = Column(String(255), nullable=True)
