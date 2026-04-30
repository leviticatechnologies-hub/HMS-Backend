"""
IPD (Inpatient Department) Management API
Comprehensive inpatient care management for Nurses and Doctors.
Handles admissions, bed management, patient monitoring, treatments, and discharge planning.

BUSINESS RULES:
- IPD is handled by NURSES and DOCTORS only
- Nurses: Patient care, vitals, medications, nursing notes, bed management
- Doctors: Medical decisions, treatments, prescriptions, discharge planning
- Both: Can view admitted patients, medical records, admission details
- Department-based access: Users can only access patients in their assigned department
"""
from typing import Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db_session
from app.core.security import get_current_user
from app.models.user import User
from app.models.patient import PatientProfile
from app.services.clinical_service import ClinicalService
from app.schemas.clinical import (
    PatientAdmissionCreate, BedAssignmentCreate, TreatmentPlanCreate,
    MedicationAdministrationCreate, NursingAssessmentCreate, DoctorRoundsCreate,
    DebugPatientEditUpdate,
)
from app.core.response_utils import success_response

router = APIRouter(prefix="/ipd-management", tags=["Patient Portal - IPD Management"])


# ============================================================================
# IPD PATIENT ADMISSIONS
# ============================================================================

@router.post("/admissions")
async def admit_patient_to_ipd(
    admission_data: PatientAdmissionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Admit patient to IPD.
    
    Access Control:
    - Only Doctors can admit patients to IPD
    - Patient must exist in the system and belong to the same hospital
    - Any doctor in the hospital can admit any patient from the same hospital
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.admit_patient_to_ipd(admission_data.dict(), current_user)
    return success_response(message="Operation completed successfully", data=result)


# ============================================================================
# DOCTOR'S AVAILABLE PATIENTS FOR IPD ADMISSION
# ============================================================================

@router.get("/available-patients")
async def get_available_patients_for_admission(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get list of patients that the doctor can see.
    
    Returns all patients in the hospital with their admission status.
    
    Access Control:
    - Only Doctors can access this endpoint
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.get_available_patients_for_admission(current_user)
    return success_response(message="Operation completed successfully", data=result)


# ============================================================================
# IPD PATIENT MANAGEMENT
# ============================================================================

@router.get("/patients")
async def get_ipd_patients(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    ward: Optional[str] = Query(None),
    condition: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get list of IPD patients in user's department.
    
    Access Control:
    - Nurses and Doctors can view IPD patients
    - Department-based access control
    """
    clinical_service = ClinicalService(db)
    filters = {
        "page": page,
        "limit": limit,
        "ward": ward,
        "condition": condition
    }
    result = await clinical_service.get_ipd_patients(filters, current_user)
    return success_response(message="Operation completed successfully", data=result)


@router.get("/admissions/{admission_number}")
async def get_ipd_admission_details(
    admission_number: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed IPD admission information.
    
    Access Control:
    - Nurses and Doctors can view admission details
    - Department-based access control
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.get_ipd_admission_details(admission_number, current_user)
    return success_response(message="Operation completed successfully", data=result)


# ============================================================================
# NURSING ASSESSMENTS (IPD)
# ============================================================================

@router.post("/nursing-assessments")
async def create_nursing_assessment(
    assessment_data: NursingAssessmentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create comprehensive nursing assessment for IPD patient.
    
    Access Control:
    - Only Nurses can create nursing assessments
    - Department-based access control
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.create_nursing_assessment(assessment_data.dict(), current_user)
    return success_response(message="Operation completed successfully", data=result)


# ============================================================================
# DOCTOR ROUNDS (IPD)
# ============================================================================

@router.post("/doctor-rounds")
async def create_doctor_rounds(
    rounds_data: DoctorRoundsCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Document doctor rounds for IPD patient.
    
    Access Control:
    - Only Doctors can document rounds
    - Department-based access control
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.create_doctor_rounds(rounds_data.dict(), current_user)
    return success_response(message="Operation completed successfully", data=result)


# ============================================================================
# IPD DASHBOARD
# ============================================================================

@router.get("/dashboard")
async def get_ipd_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get IPD dashboard with key metrics and patient information.
    
    Access Control:
    - Nurses and Doctors can access IPD dashboard
    - Department-specific metrics
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.get_ipd_dashboard(current_user)
    return success_response(message="Operation completed successfully", data=result)


# ============================================================================
# DEBUG ENDPOINT - List all patients in hospital
# ============================================================================

@router.get("/debug/all-patients")
async def debug_list_all_patients(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    DEBUG: List all patients in the hospital for testing purposes.
    """
    clinical_service = ClinicalService(db)
    user_context = clinical_service.get_user_context(current_user)
    
    # Get all patients in the hospital
    query = select(PatientProfile)
    if user_context.get("hospital_id"):
        query = query.where(PatientProfile.hospital_id == uuid.UUID(user_context["hospital_id"]))
    
    patients_result = await db.execute(
        query.options(selectinload(PatientProfile.user))
        .order_by(PatientProfile.created_at.desc())
    )
    
    patients = patients_result.scalars().all()
    
    patient_list = []
    for patient in patients:
        patient_list.append({
            "patient_id": patient.patient_id,
            "name": f"{patient.user.first_name} {patient.user.last_name}",
            "email": patient.user.email,
            "hospital_id": str(patient.hospital_id),
            "created_at": patient.created_at.isoformat()
        })
    
    return success_response(message="Operation completed successfully", data={
        "hospital_id": user_context.get("hospital_id"),
        "total_patients": len(patient_list),
        "patients": patient_list
    })


@router.patch("/debug/all-patients/{patient_ref}")
async def debug_edit_patient(
    patient_ref: str,
    payload: DebugPatientEditUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    DEBUG: Edit a patient from the debug patient list by patient_ref.
    Hospital-scoped; updates user + patient profile fields.
    """
    clinical_service = ClinicalService(db)
    user_context = clinical_service.get_user_context(current_user)
    if not user_context.get("hospital_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital context is required.",
        )

    hospital_id = uuid.UUID(user_context["hospital_id"])
    result = await db.execute(
        select(PatientProfile)
        .where(
            PatientProfile.patient_id == (patient_ref or "").strip(),
            PatientProfile.hospital_id == hospital_id,
        )
        .options(selectinload(PatientProfile.user))
    )
    patient = result.scalar_one_or_none()
    if not patient or not patient.user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient '{patient_ref}' not found in this hospital.",
        )

    data = payload.model_dump(exclude_unset=True)
    user = patient.user

    for key in ("first_name", "last_name", "phone", "email"):
        if key in data:
            setattr(user, key, data[key])

    for key in (
        "date_of_birth",
        "gender",
        "id_type",
        "id_number",
        "id_name",
        "address",
        "pincode",
        "city",
        "district",
        "state",
        "country",
        "emergency_contact_name",
        "emergency_contact_phone",
        "emergency_contact_relation",
        "medical_history",
        "blood_group",
        "blood_group_value",
    ):
        if key in data:
            setattr(patient, key, data[key])

    await db.commit()
    await db.refresh(patient)
    await db.refresh(user)

    return success_response(
        message="Patient updated successfully",
        data={
            "patient_id": patient.patient_id,
            "name": f"{user.first_name} {user.last_name}".strip(),
            "email": user.email,
            "phone": user.phone,
            "hospital_id": str(patient.hospital_id),
            "created_at": patient.created_at.isoformat() if patient.created_at else None,
        },
    )