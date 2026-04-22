"""
Service for Secure Result Access UI.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabResultAccessGrant, LabResultAccessLog
from app.schemas.lab_result_access import (
    GrantResultAccessRequest,
    GrantResultAccessResponse,
    ResultAccessDashboardResponse,
    ResultAccessLogRow,
    ResultAccessMeta,
    ResultAccessPatientRow,
    ResultAccessStatCards,
)


class LabResultAccessService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    def _demo_patients(self) -> list[ResultAccessPatientRow]:
        return [
            ResultAccessPatientRow(
                patient_ref="PAT-001",
                patient_name="Rajesh Kumar",
                email="rajesh@email.com",
                phone="+91 9876543210",
                last_access="2024-01-15 10:30",
                access_count=3,
                status="ACTIVE",
            ),
            ResultAccessPatientRow(
                patient_ref="PAT-002",
                patient_name="Priya Sharma",
                email="priya@email.com",
                phone="+91 9988776655",
                last_access="2024-01-15 09:15",
                access_count=2,
                status="ACTIVE",
            ),
            ResultAccessPatientRow(
                patient_ref="PAT-003",
                patient_name="Suresh Patel",
                email="suresh@email.com",
                phone="+91 9123456780",
                last_access="2024-01-14 16:45",
                access_count=1,
                status="ACTIVE",
            ),
        ]

    def _demo_logs(self) -> list[ResultAccessLogRow]:
        return [
            ResultAccessLogRow(
                patient_name="Rajesh Kumar",
                accessed_by="patient@email.com",
                access_time="2024-01-15 10:30",
                action="View Report",
                ip_address="192.168.1.100",
                device_browser="Mobile - Chrome",
            ),
            ResultAccessLogRow(
                patient_name="Priya Sharma",
                accessed_by="dr.sharma@hospital.com",
                access_time="2024-01-15 09:15",
                action="Download Report",
                ip_address="203.0.113.50",
                device_browser="Desktop - Firefox",
            ),
            ResultAccessLogRow(
                patient_name="Suresh Patel",
                accessed_by="patient@email.com",
                access_time="2024-01-14 16:45",
                action="View Report",
                ip_address="192.168.1.150",
                device_browser="Tablet - Safari",
            ),
        ]

    async def get_dashboard(self, *, demo: bool = False, search: str | None = None, status: str | None = None) -> ResultAccessDashboardResponse:
        if demo:
            patients = self._demo_patients()
            logs = self._demo_logs()
        else:
            patients, logs = await self._db_rows()

        if search:
            q = search.strip().lower()
            patients = [
                p
                for p in patients
                if q in p.patient_name.lower() or q in p.patient_ref.lower() or q in p.email.lower()
            ]
            logs = [
                l
                for l in logs
                if q in l.patient_name.lower() or q in l.accessed_by.lower()
            ]
        if status:
            s = status.strip().upper()
            patients = [p for p in patients if p.status == s]

        return ResultAccessDashboardResponse(
            meta=ResultAccessMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=False,
                demo_data=demo,
            ),
            stats=ResultAccessStatCards(
                active_access=sum(1 for p in patients if p.status == "ACTIVE"),
                doctor_access=12 if demo else 0,
                todays_accesses=2 if demo else 0,
                mobile_accesses=8 if demo else 0,
            ),
            patients=patients,
            access_logs=logs,
            security_features=[
                "Encrypted Links",
                "Access Control",
                "Audit Trail",
            ],
        )

    async def grant_access(self, payload: GrantResultAccessRequest) -> GrantResultAccessResponse:
        code = f"ACC-{datetime.now(timezone.utc).strftime('%H%M%S')}"
        rec = LabResultAccessGrant(
            hospital_id=self.hospital_id,
            patient_ref=payload.patient_ref,
            patient_name=payload.patient_ref,
            doctor_name=None,
            email=payload.email,
            phone="",
            access_type=payload.access_type,
            status="ACTIVE",
            access_count=0,
            access_code=code,
            expiry_date=payload.expiry_date,
            last_access=None,
        )
        self.db.add(rec)
        await self.db.commit()
        return GrantResultAccessResponse(
            message="Secure result access granted successfully.",
            patient_ref=payload.patient_ref,
            email=payload.email,
            access_type=payload.access_type,
            expiry_date=payload.expiry_date,
            access_code=code,
        )

    async def _db_rows(self):
        p_stmt = select(LabResultAccessGrant).where(LabResultAccessGrant.hospital_id == self.hospital_id)
        l_stmt = select(LabResultAccessLog).where(LabResultAccessLog.hospital_id == self.hospital_id)
        p_recs = (await self.db.execute(p_stmt)).scalars().all()
        l_recs = (await self.db.execute(l_stmt)).scalars().all()
        patients = [
            ResultAccessPatientRow(
                patient_ref=r.patient_ref,
                patient_name=r.patient_name,
                email=r.email,
                phone=r.phone or "",
                last_access=r.last_access or "",
                access_count=r.access_count,
                status=r.status,
            ) for r in p_recs
        ]
        logs = [
            ResultAccessLogRow(
                patient_name=r.patient_name,
                accessed_by=r.accessed_by,
                access_time=r.access_time,
                action=r.action,
                ip_address=r.ip_address,
                device_browser=r.device_browser,
            ) for r in l_recs
        ]
        return patients, logs

