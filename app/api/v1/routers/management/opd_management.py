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
    OpdConsultationComplete,
    OpdConsultationStart,
    OpdConsultationWithVitalsCreate,
    OpdDoctorCreate,
    OpdDoctorDeactivate,
    OpdDoctorUpdate,
    OpdDoctorConfigure,
    OpdPatientCreate,
    OpdStatusUpdate,
    OpdTokenCreate,
    OpdTransferModal,
    OpdTransferCreate,
)
from app.services.opd_management_service import OpdManagementService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/opd", tags=["OPD Management"])
doctors_router = APIRouter(prefix="/doctors", tags=["Doctor Management"])

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


@router.post("/tokens")
async def create_token(
    body: OpdTokenCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.create_token_from_modal(body.model_dump(exclude_none=True))


@router.get("/tokens")
async def get_all_tokens(
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(100, ge=1, le=300),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.list_tokens(limit=limit)


@router.get("/tokens/{id}")
async def get_token_by_id(
    id: UUID,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.get_token_by_id(id)


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


@router.delete("/tokens/{id}")
async def cancel_token(
    id: UUID,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.cancel_token(id)


@router.get("/doctors")
async def list_opd_doctors(
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.list_doctors()


@doctors_router.get("")
async def get_all_doctors(
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.list_doctors()


@doctors_router.post("")
async def create_doctor(
    body: OpdDoctorCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.create_doctor_modal(body.model_dump(exclude_none=True))


@doctors_router.put("/{id}")
async def update_doctor(
    id: UUID,
    body: OpdDoctorUpdate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.update_doctor_modal(id, body.model_dump(exclude_none=True))


@doctors_router.patch("/{id}/status")
async def update_doctor_status(
    id: UUID,
    is_active: bool = Query(..., alias="isActive"),
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.set_doctor_status(id, is_active)


@doctors_router.post("/{id}/deactivate")
async def deactivate_doctor(
    id: UUID,
    body: OpdDoctorDeactivate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.deactivate_doctor_reassign(id, body.reassignToDoctorIds)


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


@router.post("/consultations/start")
async def start_consultation(
    body: OpdConsultationStart,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.DOCTOR, UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.start_consultation_modal(body.model_dump(exclude_none=True))


@router.post("/consultations/{id}/complete")
async def complete_consultation(
    id: UUID,
    body: OpdConsultationComplete,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.DOCTOR, UserRole.HOSPITAL_ADMIN)),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.complete_consultation_modal(id, body.model_dump(exclude_none=True))


@router.get("/consultations/{id}")
async def get_consultation(
    id: UUID,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.get_consultation_by_id(id)


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


@router.post("/transfer-patient")
async def transfer_patient_modal(
    body: OpdTransferModal,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.transfer_modal(body.model_dump())


@router.get("/dashboard")
async def get_opd_dashboard_stats(
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(_opd_roles),
    db: AsyncSession = Depends(get_db_session),
):
    _ = current_user
    svc = OpdManagementService(db, _hid(context))
    return await svc.dashboard_stats()
