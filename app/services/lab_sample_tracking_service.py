"""
Service layer for Sample Tracking UI with barcode lookup and quick status actions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabSampleTracking
from app.schemas.lab_sample_tracking import (
    BarcodeLookupResponse,
    SampleActionRequest,
    SampleActionResponse,
    SampleTrackingListResponse,
    SampleTrackingMeta,
    SampleTrackingRow,
)


class LabSampleTrackingService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    def _demo_rows(self) -> list[SampleTrackingRow]:
        return [
            SampleTrackingRow(
                barcode="BC001",
                test_id="TEST-2024-001",
                patient_name="Rajesh Kumar",
                test_type="CBC",
                sample_type="Blood",
                collection_time="2024-01-15 09:30",
                status="COLLECTED",
                current_location="Collection Desk",
            ),
            SampleTrackingRow(
                barcode="BC002",
                test_id="TEST-2024-002",
                patient_name="Priya Sharma",
                test_type="Lipid Profile",
                sample_type="Blood",
                collection_time="2024-01-15 10:15",
                status="IN_TRANSIT",
                current_location="Corridor - Tube Carrier",
            ),
            SampleTrackingRow(
                barcode="BC003",
                test_id="TEST-2024-003",
                patient_name="Suresh Patel",
                test_type="Urine Culture",
                sample_type="Urine",
                collection_time="2024-01-14 14:45",
                status="IN_LAB",
                current_location="Microbiology Bench",
            ),
            SampleTrackingRow(
                barcode="BC004",
                test_id="TEST-2024-004",
                patient_name="Anita Mehta",
                test_type="Liver Function",
                sample_type="Blood",
                collection_time="2024-01-14 11:20",
                status="PROCESSED",
                current_location="Chemistry Analyzer",
            ),
        ]

    async def list_samples(self, *, demo: bool = False, search: Optional[str] = None) -> SampleTrackingListResponse:
        rows = self._demo_rows() if demo else await self._db_rows()
        if search:
            q = search.strip().lower()
            rows = [
                r
                for r in rows
                if q in r.barcode.lower()
                or q in r.patient_name.lower()
                or q in r.test_id.lower()
            ]
        return SampleTrackingListResponse(
            meta=SampleTrackingMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=False,
                demo_data=demo,
            ),
            rows=rows,
        )

    async def lookup_barcode(self, barcode: str, *, demo: bool = False) -> BarcodeLookupResponse:
        rows = self._demo_rows() if demo else await self._db_rows()
        sample = next((r for r in rows if r.barcode.upper() == barcode.upper().strip()), None)
        if not sample:
            return BarcodeLookupResponse(found=False, sample=None, message="Barcode not found.")
        return BarcodeLookupResponse(found=True, sample=sample, message="Sample found.")

    async def apply_action(self, payload: SampleActionRequest) -> SampleActionResponse:
        status_map = {
            "MARK_COLLECTED": "COLLECTED",
            "MARK_IN_TRANSIT": "IN_TRANSIT",
            "START_PROCESSING": "IN_LAB",
            "COMPLETE_TEST": "COMPLETED",
        }
        new_status = status_map[payload.action]
        location = payload.location or self._default_location_for_status(new_status)
        stmt = select(LabSampleTracking).where(
            LabSampleTracking.hospital_id == self.hospital_id,
            LabSampleTracking.barcode == payload.barcode,
        )
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row:
            row.status = new_status
            row.current_location = location
            await self.db.commit()
        return SampleActionResponse(
            message=f"{payload.barcode} updated successfully.",
            barcode=payload.barcode,
            status=new_status,  # type: ignore[arg-type]
            current_location=location,
        )

    def _default_location_for_status(self, status: str) -> str:
        defaults = {
            "COLLECTED": "Collection Desk",
            "IN_TRANSIT": "Corridor - Transfer",
            "IN_LAB": "Lab Processing Area",
            "PROCESSED": "Analyzer Completed Rack",
            "COMPLETED": "Result Dispatch Queue",
        }
        return defaults.get(status, "Lab")

    async def _db_rows(self) -> list[SampleTrackingRow]:
        stmt = (
            select(LabSampleTracking)
            .where(LabSampleTracking.hospital_id == self.hospital_id)
            .order_by(LabSampleTracking.created_at.desc())
        )
        recs = (await self.db.execute(stmt)).scalars().all()
        return [
            SampleTrackingRow(
                barcode=r.barcode,
                test_id=r.test_id,
                patient_name=r.patient_name,
                test_type=r.test_type,
                sample_type=r.sample_type,
                collection_time=r.collection_time,
                status=r.status,
                current_location=r.current_location,
            )
            for r in recs
        ]

