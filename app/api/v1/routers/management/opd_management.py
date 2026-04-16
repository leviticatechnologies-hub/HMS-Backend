"""
OPD Management — patient queue, doctors, consultation & vitals.
Requires hospital JWT context.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_hospital_context, require_roles
from app.core.database import get_db_session
from app.core.enums import UserRole
from app.models.user import User
from app.schemas.opd_management import (
    OpdConsultationWithVitalsCreate,
    OpdDoctorConfigure,
    OpdPatientCreate,
    OpdStatusUpdate,
    OpdTransferCreate,
)
from app.services.opd_management_service import OpdManagementService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/opd", tags=["OPD Management"])

_opd_roles = require_roles(
    UserRole.HOSPITAL_ADMIN,
    UserRole.RECEPTIONIST,
    UserRole.DOCTOR,
    UserRole.NURSE,
)


def _hid(context: dict) -> UUID:
    return UUID(str(context["hospital_id"]))


@router.post("/patient")
async def create_opd_patient(
    body: OpdPatientCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    """Create OPD visit + token (spec: POST /opd/patient)."""
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.create_visit(body.model_dump(exclude_none=True))


@router.get("/patients")
async def list_opd_patients(
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
    status_filter: Optional[str] = Query(None, alias="status"),
    doctor_user_id: Optional[UUID] = Query(None),
    limit: int = Query(100, ge=1, le=200),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.list_visits(status_filter=status_filter, doctor_user_id=doctor_user_id, limit=limit)


@router.put("/patient/{visit_id}/status")
async def update_opd_patient_status(
    visit_id: UUID,
    body: OpdStatusUpdate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.update_visit_status(visit_id, body.status)


@router.delete("/patient/{visit_id}")
async def delete_opd_patient(
    visit_id: UUID,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    """Cancel OPD visit (spec: DELETE)."""
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.cancel_visit(visit_id)


@router.get("/doctors")
async def list_opd_doctors(
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.list_doctors()


@router.post("/doctor")
async def configure_opd_doctor_route(
    body: OpdDoctorConfigure,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    """Attach OPD room / limits to an existing doctor user."""
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.configure_opd_doctor(
        body.doctor_user_id,
        body.model_dump(exclude_none=True),
    )


@router.put("/doctor/{doctor_user_id}/toggle-status")
async def toggle_opd_doctor(
    doctor_user_id: UUID,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.toggle_doctor_status(doctor_user_id)


@router.post("/consultation")
async def create_opd_consultation(
    body: OpdConsultationWithVitalsCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.DOCTOR, UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.create_consultation_with_vitals(body.model_dump(exclude_none=True))


@router.get("/consultation/{patient_id}")
async def get_consultations_for_patient(
    patient_id: str,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    """Recent consultations + vitals by patient profile UUID or PAT-xxx ref."""
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.get_consultation_by_patient(patient_id)


@router.post("/transfer")
async def transfer_opd_patient(
    body: OpdTransferCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.transfer_patient(body.model_dump())
