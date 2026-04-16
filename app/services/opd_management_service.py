"""OPD queue & consultation business logic (tenant-scoped)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import UserRole
from app.models.doctor import DoctorProfile
from app.models.hospital import Department
from app.models.opd_management import (
    OpdConsultation,
    OpdPatientTransfer,
    OpdTokenLog,
    OpdVisit,
    OpdVitalSign,
)
from app.models.patient import PatientProfile
from app.models.user import User


def _today_window_utc() -> Tuple[datetime, datetime]:
    today = date.today()
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _age_from_dob(dob_str: Optional[str]) -> Optional[int]:
    if not dob_str or len(str(dob_str)) < 10:
        return None
    try:
        bd = date.fromisoformat(str(dob_str)[:10])
        t = date.today()
        return t.year - bd.year - ((t.month, t.day) < (bd.month, bd.day))
    except ValueError:
        return None


class OpdManagementService:
    def __init__(self, db: AsyncSession, hospital_id: uuid.UUID):
        self.db = db
        self.hospital_id = hospital_id

    async def _get_patient_profile(
        self, patient_ref: Optional[str], patient_profile_id: Optional[uuid.UUID]
    ) -> PatientProfile:
        q = select(PatientProfile).where(PatientProfile.hospital_id == self.hospital_id)
        if patient_profile_id:
            q = q.where(PatientProfile.id == patient_profile_id)
        elif patient_ref and str(patient_ref).strip():
            q = q.where(PatientProfile.patient_id == str(patient_ref).strip())
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PATIENT_REQUIRED", "message": "Provide patient_ref or patient_profile_id"},
            )
        r = await self.db.execute(q.options(selectinload(PatientProfile.user)).limit(1))
        p = r.scalar_one_or_none()
        if not p:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found in this hospital"},
            )
        return p

    def _full_name(self, user: Optional[User]) -> str:
        if not user:
            return ""
        parts = [user.first_name or "", user.last_name or ""]
        return " ".join(x for x in parts if x).strip() or (user.email or "")

    async def _next_opd_ref(self) -> str:
        start, end = _today_window_utc()
        r = await self.db.execute(
            select(func.count(OpdVisit.id)).where(
                and_(
                    OpdVisit.hospital_id == self.hospital_id,
                    OpdVisit.created_at >= start,
                    OpdVisit.created_at < end,
                )
            )
        )
        n = (r.scalar() or 0) + 1
        return f"OPD-{date.today().strftime('%Y%m%d')}-{n:04d}"

    async def _queue_metrics_for_doctor(self, doctor_user_id: Optional[uuid.UUID]) -> Tuple[int, int]:
        """(queue_position for new patient, waiting_count) for same calendar day."""
        start, end = _today_window_utc()
        cond = [
            OpdVisit.hospital_id == self.hospital_id,
            OpdVisit.created_at >= start,
            OpdVisit.created_at < end,
            OpdVisit.status.in_(["WAITING", "IN_CONSULTATION"]),
            OpdVisit.is_active == True,
        ]
        if doctor_user_id:
            cond.append(OpdVisit.doctor_user_id == doctor_user_id)
        r = await self.db.execute(select(func.count(OpdVisit.id)).where(and_(*cond)))
        waiting = r.scalar() or 0
        return waiting + 1, waiting

    async def create_visit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        pp = await self._get_patient_profile(
            payload.get("patient_ref"),
            payload.get("patient_profile_id"),
        )
        user = pp.user
        pname = self._full_name(user)
        age = _age_from_dob(getattr(pp, "date_of_birth", None))
        opd_ref = await self._next_opd_ref()
        doctor_user_id = payload.get("doctor_user_id")
        if isinstance(doctor_user_id, str):
            doctor_user_id = uuid.UUID(doctor_user_id)

        pos, ahead = await self._queue_metrics_for_doctor(doctor_user_id)
        token_no = f"T-{date.today().strftime('%y%m%d')}-{pos:04d}"

        arrival = payload.get("arrival_time") or datetime.now(timezone.utc)

        visit = OpdVisit(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            opd_ref=opd_ref,
            patient_profile_id=pp.id,
            patient_name=pname,
            age=age,
            gender=getattr(pp, "gender", None),
            phone_no=(user.phone if user else None),
            blood_group=getattr(pp, "blood_group", None),
            token_no=token_no,
            visit_type=(payload.get("visit_type") or "NEW").upper(),
            priority=(payload.get("priority") or "NORMAL").upper(),
            department_name=(payload.get("department_name") or "").strip() or None,
            doctor_user_id=doctor_user_id,
            status="WAITING",
            queue_position=pos,
            waiting_time=max(0, (ahead * 15)),
            arrival_time=arrival,
            appointment_id=payload.get("appointment_id"),
        )
        self.db.add(visit)
        await self.db.flush()

        log = OpdTokenLog(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            token_no=token_no,
            patient_profile_id=pp.id,
            doctor_user_id=doctor_user_id,
            generated_time=arrival,
            status="ACTIVE",
            opd_visit_id=visit.id,
        )
        self.db.add(log)
        await self.db.commit()
        await self.db.refresh(visit)
        return self._visit_to_dict(visit)

    async def list_visits(
        self,
        status_filter: Optional[str] = None,
        doctor_user_id: Optional[uuid.UUID] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        q = select(OpdVisit).where(
            OpdVisit.hospital_id == self.hospital_id,
            OpdVisit.is_active == True,
        )
        if status_filter:
            q = q.where(OpdVisit.status == status_filter.upper())
        if doctor_user_id:
            q = q.where(OpdVisit.doctor_user_id == doctor_user_id)
        q = q.order_by(OpdVisit.created_at.desc()).limit(min(limit, 200))
        r = await self.db.execute(q)
        rows = r.scalars().all()
        return {"items": [self._visit_to_dict(v) for v in rows], "total": len(rows)}

    def _visit_to_dict(self, v: OpdVisit) -> Dict[str, Any]:
        return {
            "id": str(v.id),
            "opd_ref": v.opd_ref,
            "patient_profile_id": str(v.patient_profile_id),
            "patient_name": v.patient_name,
            "age": v.age,
            "gender": v.gender,
            "phone_no": v.phone_no,
            "blood_group": v.blood_group,
            "token_no": v.token_no,
            "visit_type": v.visit_type,
            "priority": v.priority,
            "department": v.department_name,
            "doctor_id": str(v.doctor_user_id) if v.doctor_user_id else None,
            "status": v.status,
            "queue_position": v.queue_position,
            "waiting_time": v.waiting_time,
            "arrival_time": v.arrival_time.isoformat() if v.arrival_time else None,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "updated_at": v.updated_at.isoformat() if v.updated_at else None,
        }

    async def update_visit_status(self, visit_id: uuid.UUID, new_status: str) -> Dict[str, Any]:
        r = await self.db.execute(
            select(OpdVisit).where(
                and_(
                    OpdVisit.id == visit_id,
                    OpdVisit.hospital_id == self.hospital_id,
                )
            )
        )
        v = r.scalar_one_or_none()
        if not v:
            raise HTTPException(status_code=404, detail={"code": "OPD_VISIT_NOT_FOUND", "message": "OPD visit not found"})
        v.status = new_status.upper()
        await self.db.commit()
        await self.db.refresh(v)
        return self._visit_to_dict(v)

    async def cancel_visit(self, visit_id: uuid.UUID) -> Dict[str, Any]:
        out = await self.update_visit_status(visit_id, "CANCELLED")
        r = await self.db.execute(
            select(OpdTokenLog).where(
                OpdTokenLog.opd_visit_id == visit_id,
                OpdTokenLog.hospital_id == self.hospital_id,
            )
        )
        for lg in r.scalars().all():
            lg.status = "CANCELLED"
        await self.db.commit()
        return {"cancelled": True, "visit": out}

    async def list_doctors(self) -> Dict[str, Any]:
        start, end = _today_window_utc()
        r = await self.db.execute(
            select(User, DoctorProfile)
            .join(DoctorProfile, DoctorProfile.user_id == User.id)
            .options(selectinload(DoctorProfile.department))
            .where(
                and_(
                    User.hospital_id == self.hospital_id,
                    DoctorProfile.hospital_id == self.hospital_id,
                )
            )
        )
        items = []
        for user, dprof in r.all():
            qn = await self.db.execute(
                select(func.count(OpdVisit.id)).where(
                    and_(
                        OpdVisit.hospital_id == self.hospital_id,
                        OpdVisit.doctor_user_id == user.id,
                        OpdVisit.created_at >= start,
                        OpdVisit.created_at < end,
                        OpdVisit.status.in_(["WAITING", "IN_CONSULTATION"]),
                    )
                )
            )
            qc = qn.scalar() or 0
            md = (user.user_metadata or {}) if isinstance(user.user_metadata, dict) else {}
            items.append(
                {
                    "id": str(user.id),
                    "name": self._full_name(user),
                    "department": dprof.department.name if dprof.department else None,
                    "specialization": dprof.specialization,
                    "opd_room": md.get("opd_room"),
                    "contact": user.phone,
                    "is_active": (getattr(user, "status", None) or "").upper() == "ACTIVE",
                    "queue_count": qc,
                    "consultation_fee": float(dprof.consultation_fee) if dprof.consultation_fee is not None else None,
                    "working_hours": md.get("opd_working_hours") or dprof.availability_time,
                    "rating": md.get("opd_rating"),
                    "experience": f"{dprof.experience_years} years" if dprof.experience_years is not None else None,
                    "max_patients_per_day": md.get("max_patients_per_day"),
                }
            )
        return {"doctors": items, "total": len(items)}

    async def configure_opd_doctor(self, doctor_user_id: uuid.UUID, body: Dict[str, Any]) -> Dict[str, Any]:
        r = await self.db.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(
                and_(
                    User.id == doctor_user_id,
                    User.hospital_id == self.hospital_id,
                )
            )
        )
        u = r.scalar_one_or_none()
        if not u:
            raise HTTPException(status_code=404, detail="Doctor user not found")
        names = [role.name for role in (u.roles or [])]
        if UserRole.DOCTOR.value not in names:
            raise HTTPException(status_code=400, detail="User is not a doctor in this hospital")
        md = dict(u.user_metadata or {})
        if body.get("opd_room"):
            md["opd_room"] = str(body["opd_room"]).strip()
        if body.get("max_patients_per_day") is not None:
            md["max_patients_per_day"] = int(body["max_patients_per_day"])
        if body.get("working_hours"):
            md["opd_working_hours"] = str(body["working_hours"]).strip()
        u.user_metadata = md
        await self.db.commit()
        return {"doctor_user_id": str(u.id), "message": "OPD doctor settings updated", "metadata": md}

    async def toggle_doctor_status(self, doctor_user_id: uuid.UUID) -> Dict[str, Any]:
        r = await self.db.execute(select(User).where(and_(User.id == doctor_user_id, User.hospital_id == self.hospital_id)))
        u = r.scalar_one_or_none()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        from app.core.enums import UserStatus

        cur = (u.status or "").upper()
        active = cur == UserStatus.ACTIVE.value
        u.status = UserStatus.BLOCKED.value if active else UserStatus.ACTIVE.value
        await self.db.commit()
        return {
            "doctor_user_id": str(u.id),
            "is_active": u.status == UserStatus.ACTIVE.value,
            "status": u.status,
        }

    async def create_consultation_with_vitals(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        vid = payload["opd_visit_id"]
        if isinstance(vid, str):
            vid = uuid.UUID(vid)
        r = await self.db.execute(
            select(OpdVisit).where(and_(OpdVisit.id == vid, OpdVisit.hospital_id == self.hospital_id))
        )
        ov = r.scalar_one_or_none()
        if not ov:
            raise HTTPException(status_code=404, detail="OPD visit not found")
        r2 = await self.db.execute(select(OpdConsultation).where(OpdConsultation.opd_visit_id == ov.id))
        if r2.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Consultation already exists for this visit")

        doc_id = (
            payload.get("doctor_user_id") or ov.doctor_user_id
        )
        if not doc_id:
            raise HTTPException(status_code=400, detail="doctor_user_id required on visit or body")
        if isinstance(doc_id, str):
            doc_id = uuid.UUID(doc_id)

        oc = OpdConsultation(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            opd_visit_id=ov.id,
            patient_profile_id=ov.patient_profile_id,
            doctor_user_id=doc_id,
            consultation_type=(payload.get("consultation_type") or "NEW").upper(),
            symptoms=payload.get("symptoms"),
            diagnosis=payload.get("diagnosis"),
            prescription=payload.get("prescription"),
            tests_recommended=payload.get("tests_recommended") or [],
            remarks=payload.get("remarks"),
            next_visit_date=payload.get("next_visit_date"),
        )
        self.db.add(oc)
        await self.db.flush()

        vit = payload.get("vitals") or {}
        if vit:
            vs = OpdVitalSign(
                id=uuid.uuid4(),
                hospital_id=self.hospital_id,
                consultation_id=oc.id,
                bp=vit.get("bp"),
                pulse=vit.get("pulse"),
                temperature=Decimal(str(vit["temperature"])) if vit.get("temperature") is not None else None,
                spo2=vit.get("spo2"),
                weight=Decimal(str(vit["weight"])) if vit.get("weight") is not None else None,
                height=Decimal(str(vit["height"])) if vit.get("height") is not None else None,
            )
            self.db.add(vs)

        ov.status = "COMPLETED"
        await self.db.commit()
        return {
            "consultation_id": str(oc.id),
            "opd_visit_id": str(ov.id),
            "message": "Consultation saved",
        }

    async def get_consultation_by_patient(self, patient_key: str) -> Dict[str, Any]:
        """patient_key: UUID of patient_profile or PAT-xxx ref."""
        q = select(PatientProfile).where(PatientProfile.hospital_id == self.hospital_id)
        try:
            uid = uuid.UUID(patient_key)
            q = q.where(PatientProfile.id == uid)
        except ValueError:
            q = q.where(PatientProfile.patient_id == patient_key.strip())
        r = await self.db.execute(q)
        pp = r.scalar_one_or_none()
        if not pp:
            raise HTTPException(status_code=404, detail="Patient not found")

        r2 = await self.db.execute(
            select(OpdConsultation)
            .where(
                and_(
                    OpdConsultation.patient_profile_id == pp.id,
                    OpdConsultation.hospital_id == self.hospital_id,
                )
            )
            .order_by(OpdConsultation.created_at.desc())
            .limit(5)
        )
        cons = r2.scalars().all()
        out = []
        for c in cons:
            vit = None
            r3 = await self.db.execute(select(OpdVitalSign).where(OpdVitalSign.consultation_id == c.id))
            vs = r3.scalar_one_or_none()
            if vs:
                vit = {
                    "bp": vs.bp,
                    "pulse": vs.pulse,
                    "temperature": float(vs.temperature) if vs.temperature is not None else None,
                    "spo2": vs.spo2,
                    "weight": float(vs.weight) if vs.weight is not None else None,
                    "height": float(vs.height) if vs.height is not None else None,
                }
            out.append(
                {
                    "id": str(c.id),
                    "opd_visit_id": str(c.opd_visit_id),
                    "consultation_type": c.consultation_type,
                    "symptoms": c.symptoms,
                    "diagnosis": c.diagnosis,
                    "prescription": c.prescription,
                    "tests_recommended": c.tests_recommended or [],
                    "remarks": c.remarks,
                    "next_visit_date": c.next_visit_date.isoformat() if c.next_visit_date else None,
                    "vitals": vit,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
            )
        return {"patient_profile_id": str(pp.id), "consultations": out}

    async def transfer_patient(self, body: Dict[str, Any]) -> Dict[str, Any]:
        vid = body["opd_visit_id"]
        if isinstance(vid, str):
            vid = uuid.UUID(vid)
        to_doc = body["to_doctor_user_id"]
        if isinstance(to_doc, str):
            to_doc = uuid.UUID(to_doc)

        r = await self.db.execute(
            select(OpdVisit).where(and_(OpdVisit.id == vid, OpdVisit.hospital_id == self.hospital_id))
        )
        ov = r.scalar_one_or_none()
        if not ov:
            raise HTTPException(status_code=404, detail="OPD visit not found")
        fr = ov.doctor_user_id
        ov.doctor_user_id = to_doc
        tr = OpdPatientTransfer(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            opd_visit_id=ov.id,
            patient_profile_id=ov.patient_profile_id,
            from_doctor_user_id=fr,
            to_doctor_user_id=to_doc,
            reason=(body.get("reason") or "").strip() or None,
            transferred_at=datetime.now(timezone.utc),
        )
        self.db.add(tr)
        await self.db.commit()
        return {"message": "Patient transferred", "opd_visit_id": str(ov.id), "new_doctor_user_id": str(to_doc)}
