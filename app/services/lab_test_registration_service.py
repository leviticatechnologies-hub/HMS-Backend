"""
Service layer for lab test registration UI.
Uses demo/static rows until full lab order tables are reinstated.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabTestRegistration
from app.schemas.lab_test_registration import (
    RegisterTestRequest,
    RegisterTestResponse,
    TestRegistrationListResponse,
    TestRegistrationMeta,
    TestRegistrationRow,
    TestRegistrationSummary,
)


class LabTestRegistrationService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    def _demo_rows(self) -> list[TestRegistrationRow]:
        return [
            TestRegistrationRow(
                test_id="TEST-2024-001",
                patient_name="Rajesh Kumar",
                test_type="CBC",
                sample_type="Blood",
                registered_date=date(2024, 1, 15),
                status="SAMPLE_PENDING",
                priority="URGENT",
            ),
            TestRegistrationRow(
                test_id="TEST-2024-002",
                patient_name="Priya Sharma",
                test_type="Lipid Profile",
                sample_type="Blood",
                registered_date=date(2024, 1, 15),
                status="SAMPLE_COLLECTED",
                priority="ROUTINE",
            ),
            TestRegistrationRow(
                test_id="TEST-2024-003",
                patient_name="Suresh Patel",
                test_type="Urine Culture",
                sample_type="Urine",
                registered_date=date(2024, 1, 14),
                status="IN_PROGRESS",
                priority="ROUTINE",
            ),
            TestRegistrationRow(
                test_id="TEST-2024-004",
                patient_name="Anita Mehta",
                test_type="Liver Function",
                sample_type="Blood",
                registered_date=date(2024, 1, 14),
                status="COMPLETED",
                priority="URGENT",
            ),
        ]

    async def list_tests(
        self,
        *,
        for_date: Optional[date] = None,
        demo: bool = False,
        search: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> TestRegistrationListResponse:
        d = for_date or datetime.now(timezone.utc).date()
        rows = self._demo_rows() if demo else await self._db_rows()

        if search:
            q = search.strip().lower()
            rows = [
                r
                for r in rows
                if q in r.patient_name.lower()
                or q in r.test_id.lower()
                or q in r.test_type.lower()
            ]
        if status:
            s = status.strip().upper()
            rows = [r for r in rows if r.status == s]
        if priority:
            p = priority.strip().upper()
            rows = [r for r in rows if r.priority == p]

        summary = TestRegistrationSummary(
            total_tests_today=len(rows),
            completed_tests=sum(1 for r in rows if r.status == "COMPLETED"),
            in_progress_tests=sum(1 for r in rows if r.status == "IN_PROGRESS"),
            urgent_tests=sum(1 for r in rows if r.priority == "URGENT"),
        )

        return TestRegistrationListResponse(
            meta=TestRegistrationMeta(
                generated_at=datetime.now(timezone.utc),
                for_date=d,
                live_data=False,
                demo_data=demo,
            ),
            summary=summary,
            rows=rows,
        )

    async def register_test(self, payload: RegisterTestRequest) -> RegisterTestResponse:
        fake_id = f"TEST-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        row = LabTestRegistration(
            hospital_id=self.hospital_id,
            test_id=fake_id,
            patient_ref=payload.patient_ref,
            patient_name=payload.patient_name,
            doctor_name=payload.referring_doctor,
            test_type=payload.test_type,
            sample_type=payload.sample_type,
            priority=payload.priority,
            status="SAMPLE_PENDING",
            special_instructions=payload.special_instructions,
            registered_date=datetime.now(timezone.utc).date(),
        )
        self.db.add(row)
        await self.db.commit()
        return RegisterTestResponse(
            message="Test registered successfully.",
            test_id=fake_id,
            status="SAMPLE_PENDING",
            patient_ref=payload.patient_ref,
            patient_name=payload.patient_name,
            test_type=payload.test_type,
            sample_type=payload.sample_type,
            priority=payload.priority,
            referring_doctor=payload.referring_doctor,
            special_instructions=payload.special_instructions,
        )

    async def _db_rows(self) -> list[TestRegistrationRow]:
        stmt = (
            select(LabTestRegistration)
            .where(LabTestRegistration.hospital_id == self.hospital_id)
            .order_by(LabTestRegistration.created_at.desc())
        )
        recs = (await self.db.execute(stmt)).scalars().all()
        return [
            TestRegistrationRow(
                test_id=r.test_id,
                patient_name=r.patient_name,
                test_type=r.test_type,
                sample_type=r.sample_type,
                registered_date=r.registered_date,
                status=r.status,
                priority=r.priority,
            )
            for r in recs
        ]

