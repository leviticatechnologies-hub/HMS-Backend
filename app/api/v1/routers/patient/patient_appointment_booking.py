"""
Patient Appointment Booking API (TENANT FIXED)
Multi-tenant safe: ALL APPOINTMENTS STORED ONLY IN TENANT DB
"""

import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_platform_db_session
from app.models.hospital import Department, Hospital
from app.models.patient import PatientProfile, Appointment
from app.core.enums import AppointmentStatus
from app.dependencies.auth import get_current_patient, get_current_user
from app.schemas.patient_care import (
    AppointmentBookingCreate,
    AppointmentCancellationCreate,
    PatientAppointmentUpdate,
)
from app.services.appointment_service import AppointmentService
from app.database.session import (
    get_tenant_session_factory,
    resolve_tenant_database_name_for_hospital
)

router = APIRouter(
    prefix="/patient-appointment-booking",
    tags=["Patient Portal - Appointment Booking"]
)

# ==========================
# HELPERS
# ==========================
async def _get_patient_hospital(
    current_patient: PatientProfile,
    db: AsyncSession
):
    patient = await db.get(PatientProfile, current_patient.id)

    if not patient:
        raise HTTPException(404, "Patient not found")

    hospital = await db.get(Hospital, patient.hospital_id)

    if not hospital:
        raise HTTPException(404, "Hospital not found")

    return hospital


def _normalize_time(raw: str):
    raw = (raw or "").strip()
    parts = raw.split(":")

    if len(parts) < 2:
        raise HTTPException(
            status_code=400,
            detail="Invalid time format. Use HH:MM or HH:MM:SS"
        )

    h = int(parts[0])
    m = int(parts[1])
    s = int(parts[2]) if len(parts) == 3 else 0

    return f"{h:02d}:{m:02d}:{s:02d}"


# ==========================
# DEPARTMENTS (platform read ok)
# ==========================
@router.get("/departments")
async def get_departments(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    hospital = await _get_patient_hospital(current_patient, db)

    result = await db.execute(
        select(Department).where(
            and_(
                Department.hospital_id == hospital.id,
                Department.is_active == True
            )
        )
    )

    return result.scalars().all()


# ==========================
# BOOK APPOINTMENT (TENANT WRITE)
# ==========================
@router.post("/book-appointment")
async def book_appointment(
    booking_request: AppointmentBookingCreate,
    current_patient: PatientProfile = Depends(get_current_patient),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    hospital = await _get_patient_hospital(current_patient, db)

    tenant_db_name = await resolve_tenant_database_name_for_hospital(
        str(hospital.id)
    )

    if not tenant_db_name:
        raise HTTPException(500, "Tenant DB not configured")

    async_session = get_tenant_session_factory(tenant_db_name)

    async with async_session() as tenant_db:

        service = AppointmentService(tenant_db)

        return await service.create_appointment(
            patient_user_id=str(current_user.id),
            department_id=str(booking_request.department_id),
            doctor_id=str(booking_request.doctor_id),
            appointment_date=booking_request.appointment_date,
            appointment_time=booking_request.appointment_time,
            chief_complaint=booking_request.chief_complaint,
            hospital_id=str(hospital.id)
        )


# ==========================
# GET APPOINTMENT (TENANT READ)
# ==========================
@router.get("/appointment/{appointment_ref}")
async def get_appointment_details(
    appointment_ref: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    hospital = await _get_patient_hospital(current_patient, db)

    tenant_db_name = await resolve_tenant_database_name_for_hospital(
        str(hospital.id)
    )

    async_session = get_tenant_session_factory(tenant_db_name)

    async with async_session() as tenant_db:

        result = await tenant_db.execute(
            select(Appointment).where(
                Appointment.appointment_ref == appointment_ref
            )
        )

        appointment = result.scalar_one_or_none()

        if not appointment:
            raise HTTPException(404, "Appointment not found")

        return appointment


# ==========================
# UPDATE APPOINTMENT (TENANT)
# ==========================
@router.patch("/appointment/{appointment_ref}")
async def update_appointment(
    appointment_ref: str,
    body: PatientAppointmentUpdate,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    payload = body.model_dump(exclude_unset=True)

    hospital = await _get_patient_hospital(current_patient, db)

    tenant_db_name = await resolve_tenant_database_name_for_hospital(
        str(hospital.id)
    )

    async_session = get_tenant_session_factory(tenant_db_name)

    async with async_session() as tenant_db:

        result = await tenant_db.execute(
            select(Appointment).where(
                Appointment.appointment_ref == appointment_ref
            )
        )

        appointment = result.scalar_one_or_none()

        if not appointment:
            raise HTTPException(404, "Appointment not found")

        if "appointment_time" in payload:
            appointment.appointment_time = _normalize_time(payload["appointment_time"])

        if "appointment_date" in payload:
            appointment.appointment_date = payload["appointment_date"]

        if "chief_complaint" in payload:
            appointment.chief_complaint = payload["chief_complaint"]

        await tenant_db.commit()

        return {"success": True, "message": "Updated successfully"}


# ==========================
# CANCEL APPOINTMENT (TENANT)
# ==========================
@router.patch("/appointment/{appointment_ref}/cancel")
async def cancel_appointment(
    appointment_ref: str,
    cancellation_request: AppointmentCancellationCreate,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    hospital = await _get_patient_hospital(current_patient, db)

    tenant_db_name = await resolve_tenant_database_name_for_hospital(
        str(hospital.id)
    )

    async_session = get_tenant_session_factory(tenant_db_name)

    async with async_session() as tenant_db:

        result = await tenant_db.execute(
            select(Appointment).where(
                Appointment.appointment_ref == appointment_ref
            )
        )

        appointment = result.scalar_one_or_none()

        if not appointment:
            raise HTTPException(404, "Appointment not found")

        appointment.status = AppointmentStatus.CANCELLED
        appointment.cancellation_reason = cancellation_request.cancellation_reason
        appointment.cancelled_at = datetime.utcnow()

        await tenant_db.commit()

        return {"success": True, "message": "Cancelled successfully"}


# ==========================
# MY APPOINTMENTS (TENANT)
# ==========================
@router.get("/my-appointments")
async def my_appointments(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    hospital = await _get_patient_hospital(current_patient, db)

    tenant_db_name = await resolve_tenant_database_name_for_hospital(
        str(hospital.id)
    )

    async_session = get_tenant_session_factory(tenant_db_name)

    async with async_session() as tenant_db:

        result = await tenant_db.execute(
            select(Appointment).where(
                Appointment.patient_id == current_patient.id
            )
        )

        return {
            "appointments": result.scalars().all()
        }