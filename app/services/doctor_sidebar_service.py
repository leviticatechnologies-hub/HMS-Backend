"""Doctor sidebar: prescriptions, lab orders on medical records, IPD admissions, messaging, profile."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.doctor import DoctorProfile, Prescription, PrescriptionNotification
from app.models.patient import Admission, MedicalRecord, PatientProfile
from app.models.telemedicine import TelemedNotification
from app.models.user import User
from app.core.enums import AdmissionType
from app.schemas.doctor_sidebar import (
    DoctorInpatientVisitOut,
    DoctorLabResultItemOut,
    DoctorMessageOut,
    DoctorPrescriptionSummaryOut,
    DoctorProfileOut,
    DoctorProfileUpdate,
)


async def _prescription_doctor_ids(db: AsyncSession, user: User, hospital_id: uuid.UUID) -> List[uuid.UUID]:
    """prescriptions.doctor_id FK -> doctor_profiles.id; include user.id only for legacy rows."""
    ids: List[uuid.UUID] = [user.id]
    r = await db.execute(
        select(DoctorProfile.id).where(
            DoctorProfile.user_id == user.id,
            DoctorProfile.hospital_id == hospital_id,
        )
    )
    row = r.scalar_one_or_none()
    if row and row not in ids:
        ids.append(row)
    return ids


async def list_prescriptions_for_doctor(
    db: AsyncSession,
    user: User,
    hospital_id: uuid.UUID,
    patient_ref: Optional[str] = None,
    is_dispensed: Optional[bool] = None,
    limit: int = 50,
) -> List[DoctorPrescriptionSummaryOut]:
    doc_ids = await _prescription_doctor_ids(db, user, hospital_id)
    conditions: List = [
        Prescription.hospital_id == hospital_id,
        Prescription.doctor_id.in_(doc_ids),
    ]
    if patient_ref:
        pr = await db.execute(
            select(PatientProfile.id).where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == hospital_id,
                )
            )
        )
        pid = pr.scalar_one_or_none()
        if pid:
            conditions.append(Prescription.patient_id == pid)
        else:
            return []
    if is_dispensed is not None:
        conditions.append(Prescription.is_dispensed == is_dispensed)

    result = await db.execute(
        select(Prescription)
        .where(and_(*conditions))
        .options(selectinload(Prescription.patient).selectinload(PatientProfile.user))
        .order_by(desc(Prescription.created_at))
        .limit(min(limit, 100))
    )
    rows = result.scalars().all()
    out: List[DoctorPrescriptionSummaryOut] = []
    for p in rows:
        patient = p.patient
        name = ""
        pref = ""
        if patient and patient.user:
            name = f"{patient.user.first_name} {patient.user.last_name}".strip()
            pref = patient.patient_id or ""
        meds = p.medications or []
        out.append(
            DoctorPrescriptionSummaryOut(
                prescription_id=str(p.id),
                prescription_number=p.prescription_number,
                patient_ref=pref,
                patient_name=name,
                prescription_date=p.prescription_date,
                diagnosis=p.diagnosis,
                total_medicines=len(meds) if isinstance(meds, list) else 0,
                is_dispensed=bool(p.is_dispensed),
                created_at=p.created_at.isoformat() if p.created_at else "",
            )
        )
    return out


async def list_lab_results_for_doctor(
    db: AsyncSession,
    user: User,
    hospital_id: uuid.UUID,
    limit: int = 50,
) -> List[DoctorLabResultItemOut]:
    result = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.hospital_id == hospital_id,
                MedicalRecord.doctor_id == user.id,
            )
        )
        .options(selectinload(MedicalRecord.patient).selectinload(PatientProfile.user))
        .order_by(desc(MedicalRecord.created_at))
        .limit(200)
    )
    records = result.scalars().all()
    out: List[DoctorLabResultItemOut] = []
    for mr in records:
        lab = mr.lab_orders or []
        if not lab:
            continue
        patient = mr.patient
        name = ""
        pref = ""
        if patient and patient.user:
            name = f"{patient.user.first_name} {patient.user.last_name}".strip()
            pref = patient.patient_id or ""
        out.append(
            DoctorLabResultItemOut(
                medical_record_id=str(mr.id),
                patient_ref=pref,
                patient_name=name,
                recorded_at=mr.created_at.isoformat() if mr.created_at else None,
                lab_orders=list(lab) if isinstance(lab, list) else [lab],
            )
        )
        if len(out) >= limit:
            break
    return out


async def list_inpatient_visits_for_doctor(
    db: AsyncSession,
    user: User,
    hospital_id: uuid.UUID,
    active_only: bool = False,
    limit: int = 100,
) -> List[DoctorInpatientVisitOut]:
    conditions = [
        Admission.hospital_id == hospital_id,
        Admission.doctor_id == user.id,
        Admission.admission_type == AdmissionType.IPD.value,
    ]
    if active_only:
        conditions.append(Admission.is_active == True)

    result = await db.execute(
        select(Admission)
        .where(and_(*conditions))
        .options(selectinload(Admission.patient).selectinload(PatientProfile.user))
        .order_by(desc(Admission.admission_date))
        .limit(min(limit, 200))
    )
    admissions = result.scalars().all()
    out: List[DoctorInpatientVisitOut] = []
    for adm in admissions:
        patient = adm.patient
        pname = ""
        pref = ""
        if patient and patient.user:
            pname = f"{patient.user.first_name} {patient.user.last_name}".strip()
            pref = patient.patient_id or ""
        adm_date = adm.admission_date
        date_str = adm_date.date().isoformat() if adm_date else ""
        status_val = adm.status if hasattr(adm, "status") else "UNKNOWN"
        out.append(
            DoctorInpatientVisitOut(
                admission_id=str(adm.id),
                admission_number=adm.admission_number,
                patient_ref=pref,
                patient_name=pname,
                admission_date=date_str,
                admission_type=adm.admission_type,
                status=str(status_val),
                ward=adm.ward,
                room_number=adm.room_number,
                bed_number=adm.bed_number,
                chief_complaint=adm.chief_complaint or "",
                is_active=bool(adm.is_active),
            )
        )
    return out


def _dt_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


async def list_messages_for_doctor(
    db: AsyncSession,
    user: User,
    hospital_id: uuid.UUID,
    limit: int = 100,
    unread_only: bool = False,
) -> List[DoctorMessageOut]:
    tq = select(TelemedNotification).where(
        TelemedNotification.hospital_id == hospital_id,
        TelemedNotification.recipient_user_id == user.id,
        TelemedNotification.is_active == True,
    )
    if unread_only:
        tq = tq.where(TelemedNotification.read_at.is_(None))

    pq = select(PrescriptionNotification).where(
        PrescriptionNotification.hospital_id == hospital_id,
        PrescriptionNotification.recipient_user_id == user.id,
        PrescriptionNotification.is_active == True,
    )
    if unread_only:
        pq = pq.where(PrescriptionNotification.read_at.is_(None))

    tr = await db.execute(tq.order_by(desc(TelemedNotification.created_at)).limit(limit))
    pr = await db.execute(pq.order_by(desc(PrescriptionNotification.created_at)).limit(limit))
    telemed = tr.scalars().all()
    presc = pr.scalars().all()

    merged: List[tuple[str, datetime, DoctorMessageOut]] = []
    for n in telemed:
        ts = n.created_at or datetime.now(timezone.utc)
        merged.append(
            (
                "t",
                ts,
                DoctorMessageOut(
                    id=str(n.id),
                    source="telemed",
                    title=n.title,
                    body=n.body,
                    event_type=n.event_type,
                    read_at=_dt_iso(n.read_at),
                    created_at=n.created_at.isoformat() if n.created_at else "",
                ),
            )
        )
    for n in presc:
        ts = n.created_at or datetime.now(timezone.utc)
        merged.append(
            (
                "p",
                ts,
                DoctorMessageOut(
                    id=str(n.id),
                    source="prescription",
                    title=n.title,
                    body=n.body,
                    event_type=n.event_type,
                    read_at=_dt_iso(n.read_at),
                    created_at=n.created_at.isoformat() if n.created_at else "",
                ),
            )
        )
    merged.sort(key=lambda x: x[1], reverse=True)
    return [m[2] for m in merged[:limit]]


async def mark_message_read(
    db: AsyncSession,
    user: User,
    hospital_id: uuid.UUID,
    source: str,
    message_id: uuid.UUID,
) -> bool:
    now = datetime.now(timezone.utc)
    if source == "telemed":
        r = await db.execute(
            select(TelemedNotification).where(
                TelemedNotification.id == message_id,
                TelemedNotification.hospital_id == hospital_id,
                TelemedNotification.recipient_user_id == user.id,
                TelemedNotification.is_active == True,
            )
        )
        n = r.scalar_one_or_none()
        if not n:
            return False
        n.read_at = now
        await db.commit()
        return True
    if source == "prescription":
        r = await db.execute(
            select(PrescriptionNotification).where(
                PrescriptionNotification.id == message_id,
                PrescriptionNotification.hospital_id == hospital_id,
                PrescriptionNotification.recipient_user_id == user.id,
                PrescriptionNotification.is_active == True,
            )
        )
        n = r.scalar_one_or_none()
        if not n:
            return False
        n.read_at = now
        await db.commit()
        return True
    return False


def _doctor_profile_base_filter(user: User):
    """One doctor_profiles row per user (unique user_id); do not filter hospital — avoids false 404."""
    return DoctorProfile.user_id == user.id


async def ensure_doctor_profile_row(db: AsyncSession, user: User) -> None:
    """
    Ensure doctor_profiles exists — same bootstrap as `/simple-prescription` (department assignment required).
    Call before profile GET/PATCH when no row yet.
    """
    from app.api.v1.routers.doctor.simple_prescription import get_doctor_profile
    from app.core.enums import UserRole

    user_roles = [r.name for r in user.roles] if user.roles else []
    if UserRole.DOCTOR.value not in user_roles:
        return
    user_context = {
        "user_id": str(user.id),
        "hospital_id": str(user.hospital_id) if user.hospital_id else None,
        "role": UserRole.DOCTOR.value,
        "all_roles": user_roles,
    }
    await get_doctor_profile(user_context, db)
    q = DoctorProfile.user_id == user.id
    if user.hospital_id is not None:
        q = and_(q, DoctorProfile.hospital_id == user.hospital_id)
    return q


async def get_doctor_sidebar_profile(db: AsyncSession, user: User) -> Optional[DoctorProfileOut]:
    r = await db.execute(
        select(DoctorProfile)
        .where(_doctor_profile_base_filter(user))
        .options(selectinload(DoctorProfile.user), selectinload(DoctorProfile.department))
    )
    dp = r.scalar_one_or_none()
    if not dp:
        return None
    u = dp.user
    dept_name = dp.department.name if dp.department else None
    quals = dp.qualifications if isinstance(dp.qualifications, list) else []
    fee = float(dp.consultation_fee) if dp.consultation_fee is not None else None
    return DoctorProfileOut(
        user_id=str(user.id),
        doctor_profile_id=str(dp.id),
        email=u.email if u else user.email,
        phone=u.phone if u else user.phone,
        first_name=u.first_name if u else user.first_name,
        last_name=u.last_name if u else user.last_name,
        staff_id=u.staff_id if u else user.staff_id,
        department=dept_name,
        specialization=dp.specialization,
        designation=dp.designation,
        qualifications=list(quals),
        consultation_fee=fee,
        availability_time=dp.availability_time,
        bio=dp.bio,
        avatar_url=u.avatar_url if u else user.avatar_url,
    )


async def update_doctor_sidebar_profile(
    db: AsyncSession,
    user: User,
    payload: DoctorProfileUpdate,
) -> Optional[DoctorProfileOut]:
    r = await db.execute(
        select(DoctorProfile)
        .where(_doctor_profile_base_filter(user))
        .options(selectinload(DoctorProfile.user))
    )
    dp = r.scalar_one_or_none()
    if not dp:
        return None

    u = await db.get(User, user.id)
    if not u:
        return None

    if payload.phone is not None:
        u.phone = payload.phone
    if payload.first_name is not None:
        u.first_name = payload.first_name
    if payload.last_name is not None:
        u.last_name = payload.last_name
    if payload.avatar_url is not None:
        u.avatar_url = payload.avatar_url
    if payload.bio is not None:
        dp.bio = payload.bio

    await db.commit()
    await db.refresh(dp)
    return await get_doctor_sidebar_profile(db, user)
