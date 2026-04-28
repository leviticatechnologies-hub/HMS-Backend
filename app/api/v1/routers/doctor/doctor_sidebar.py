"""
Doctor Portal — unified sidebar endpoints.

Aggregates stable paths for the doctor UI sidebar:
prescriptions, lab-related orders on medical records, IPD admissions,
in-app messaging (telemed + prescription notifications), and profile.

Create/update prescriptions remain under `/simple-prescription`; list/detail here mirrors doctor scope.
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_doctor, require_hospital_context
from app.core.database import get_db_session
from app.models.user import User
from app.schemas.doctor_sidebar import (
    DoctorInpatientVisitOut,
    DoctorLabResultItemOut,
    DoctorMessageOut,
    DoctorMessageReadRequest,
    DoctorPrescriptionSummaryOut,
    DoctorProfileOut,
    DoctorProfileUpdate,
)
import app.services.doctor_sidebar_service as sidebar_svc

router = APIRouter(prefix="/doctor-sidebar", tags=["Doctor Portal - Sidebar"])


@router.get(
    "/prescriptions",
    response_model=List[DoctorPrescriptionSummaryOut],
    summary="List my prescriptions (sidebar)",
)
async def sidebar_prescriptions(
    patient_ref: Optional[str] = Query(None),
    is_dispensed: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(require_doctor()),
    ctx: Dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Doctor-scoped prescriptions; same data as `/simple-prescription/doctor/prescriptions`."""
    hid = uuid.UUID(ctx["hospital_id"])
    return await sidebar_svc.list_prescriptions_for_doctor(
        db, user, hid, patient_ref=patient_ref, is_dispensed=is_dispensed, limit=limit
    )


@router.get(
    "/lab-results",
    response_model=List[DoctorLabResultItemOut],
    summary="Lab orders on my medical records",
)
async def sidebar_lab_results(
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(require_doctor()),
    ctx: Dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Medical records authored by this doctor with non-empty `lab_orders`."""
    hid = uuid.UUID(ctx["hospital_id"])
    return await sidebar_svc.list_lab_results_for_doctor(db, user, hid, limit=limit)


@router.get(
    "/inpatient-visits",
    response_model=List[DoctorInpatientVisitOut],
    summary="My inpatient (IPD) admissions",
)
async def sidebar_inpatient_visits(
    active_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=200),
    user: User = Depends(require_doctor()),
    ctx: Dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """IPD admissions where this doctor is the admitting doctor."""
    hid = uuid.UUID(ctx["hospital_id"])
    return await sidebar_svc.list_inpatient_visits_for_doctor(
        db, user, hid, active_only=active_only, limit=limit
    )


@router.get(
    "/messages",
    response_model=List[DoctorMessageOut],
    summary="In-app messages inbox",
)
async def sidebar_messages(
    limit: int = Query(100, ge=1, le=200),
    unread_only: bool = Query(False),
    user: User = Depends(require_doctor()),
    ctx: Dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Telemedicine notifications and prescription notifications for the current user."""
    hid = uuid.UUID(ctx["hospital_id"])
    return await sidebar_svc.list_messages_for_doctor(
        db, user, hid, limit=limit, unread_only=unread_only
    )


@router.post(
    "/messages/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark one message as read",
)
async def sidebar_mark_message_read(
    body: DoctorMessageReadRequest,
    user: User = Depends(require_doctor()),
    ctx: Dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    hid = uuid.UUID(ctx["hospital_id"])
    ok = await sidebar_svc.mark_message_read(db, user, hid, body.source, body.message_id)
    mid = uuid.UUID(body.message_id)
    ok = await sidebar_svc.mark_message_read(db, user, hid, body.source, mid)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")


@router.get(
    "/profile",
    response_model=DoctorProfileOut,
    summary="My doctor profile",
)
async def sidebar_get_profile(
    user: User = Depends(require_doctor()),
    _ctx: Dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    out = await sidebar_svc.get_doctor_sidebar_profile(db, user)
    if not out:
        await sidebar_svc.ensure_doctor_profile_row(db, user)
        out = await sidebar_svc.get_doctor_sidebar_profile(db, user)
    if not out:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor profile not found. Ensure the account is assigned to a department, or contact admin.",
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor profile not found for this account.",
        )
    return out


@router.patch(
    "/profile",
    response_model=DoctorProfileOut,
    summary="Update my profile (limited fields)",
)
async def sidebar_patch_profile(
    payload: DoctorProfileUpdate,
    user: User = Depends(require_doctor()),
    _ctx: Dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    out = await sidebar_svc.update_doctor_sidebar_profile(db, user, payload)
    if not out:
        await sidebar_svc.ensure_doctor_profile_row(db, user)
        out = await sidebar_svc.update_doctor_sidebar_profile(db, user, payload)
    if not out:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor profile not found. Ensure the account is assigned to a department, or contact admin.",
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor profile not found for this account.",
        )
    return out
