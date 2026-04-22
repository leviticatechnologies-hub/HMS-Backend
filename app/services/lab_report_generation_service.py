"""
Service layer for Report Generation UI.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabReportReadyTest, LabReportRecord
from app.schemas.lab_report_generation import (
    GenerateReportRequest,
    GenerateReportResponse,
    PrintReportResponse,
    ReadyTestForReportRow,
    ReadyTestsResponse,
    ReportGenerationListResponse,
    ReportGenerationMeta,
    ReportGenerationRow,
    ReportGenerationSummary,
    ReportPreviewResponse,
)

_ALLOWED_TEMPLATES = {"STANDARD", "COMPREHENSIVE", "DOCTOR_SUMMARY", "PATIENT_FRIENDLY", "CUSTOM"}


def _normalize_template(value: str) -> str:
    v = (value or "").strip().upper()
    return v if v in _ALLOWED_TEMPLATES else "STANDARD"


class LabReportGenerationService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    def _demo_rows(self) -> list[ReportGenerationRow]:
        return [
            ReportGenerationRow(
                report_id="REP-2024-001",
                patient_name="Rajesh Kumar",
                test_type="CBC",
                completion_date=date(2024, 1, 15),
                status="READY",
                verified_by="Dr. Sharma",
            ),
            ReportGenerationRow(
                report_id="REP-2024-002",
                patient_name="Priya Sharma",
                test_type="Lipid Profile",
                completion_date=date(2024, 1, 15),
                status="PENDING_REVIEW",
                verified_by=None,
            ),
            ReportGenerationRow(
                report_id="REP-2024-003",
                patient_name="Suresh Patel",
                test_type="Kidney Function",
                completion_date=date(2024, 1, 14),
                status="READY",
                verified_by="Dr. Mehta",
            ),
            ReportGenerationRow(
                report_id="REP-2024-004",
                patient_name="Anita Mehta",
                test_type="Liver Function",
                completion_date=date(2024, 1, 14),
                status="READY",
                verified_by="Dr. Rao",
            ),
        ]

    def _demo_ready_tests(self) -> list[ReadyTestForReportRow]:
        return [
            ReadyTestForReportRow(
                patient_name="Amit Shah",
                patient_ref="PAT-005",
                test_type="Diabetes Panel",
                completed_on=date(2024, 1, 16),
                source_test_id="TEST-2024-005",
            ),
            ReadyTestForReportRow(
                patient_name="Meera Rai",
                patient_ref="PAT-006",
                test_type="Thyroid Profile",
                completed_on=date(2024, 1, 16),
                source_test_id="TEST-2024-006",
            ),
            ReadyTestForReportRow(
                patient_name="Sanjay Dutt",
                patient_ref="PAT-007",
                test_type="Kidney Function",
                completed_on=date(2024, 1, 17),
                source_test_id="TEST-2024-007",
            ),
        ]

    async def list_reports(
        self,
        *,
        demo: bool = False,
        search: Optional[str] = None,
        template: str = "STANDARD",
    ) -> ReportGenerationListResponse:
        rows = self._demo_rows() if demo else await self._db_rows()
        if search:
            q = search.strip().lower()
            rows = [
                r
                for r in rows
                if q in r.patient_name.lower()
                or q in r.report_id.lower()
                or q in r.test_type.lower()
            ]
        summary = ReportGenerationSummary(
            total_reports=len(rows),
            ready_reports=sum(1 for r in rows if r.status == "READY"),
            pending_review=sum(1 for r in rows if r.status == "PENDING_REVIEW"),
            test_types=len({r.test_type for r in rows}),
        )
        return ReportGenerationListResponse(
            meta=ReportGenerationMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=False,
                demo_data=demo,
            ),
            selected_template=_normalize_template(template),  # type: ignore[arg-type]
            templates=["STANDARD", "COMPREHENSIVE", "DOCTOR_SUMMARY", "PATIENT_FRIENDLY", "CUSTOM"],
            summary=summary,
            rows=rows,
        )

    async def ready_tests(self, *, demo: bool = False) -> ReadyTestsResponse:
        if demo:
            return ReadyTestsResponse(rows=self._demo_ready_tests())
        stmt = select(LabReportReadyTest).where(LabReportReadyTest.hospital_id == self.hospital_id)
        recs = (await self.db.execute(stmt)).scalars().all()
        return ReadyTestsResponse(rows=[
            ReadyTestForReportRow(
                patient_name=r.patient_name,
                patient_ref=r.patient_ref or "",
                test_type=r.test_type,
                completed_on=r.completed_on,
                source_test_id=r.source_test_id,
            ) for r in recs
        ])

    async def generate(self, payload: GenerateReportRequest) -> GenerateReportResponse:
        rid = f"REP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        rec = LabReportRecord(
            hospital_id=self.hospital_id,
            report_id=rid,
            patient_ref="",
            patient_name="Generated Patient",
            doctor_name=None,
            test_type=payload.source_test_id,
            completion_date=datetime.now(timezone.utc).date(),
            status="READY",
            verified_by=None,
            template=payload.template,
        )
        self.db.add(rec)
        await self.db.commit()
        return GenerateReportResponse(
            message="Report generated successfully.",
            report_id=rid,
            status="READY",
        )

    async def preview(self, report_id: str, *, template: str = "STANDARD") -> ReportPreviewResponse:
        stmt = select(LabReportRecord).where(
            LabReportRecord.hospital_id == self.hospital_id,
            LabReportRecord.report_id == report_id,
        )
        rec = (await self.db.execute(stmt)).scalar_one_or_none()
        return ReportPreviewResponse(
            report_id=report_id,
            title=f"Lab Report - {report_id}",
            patient_name=rec.patient_name if rec else "Demo Patient",
            test_type=rec.test_type if rec else "Comprehensive Panel",
            status=rec.status if rec else "READY",
            template=_normalize_template(template),  # type: ignore[arg-type]
            preview_text="This is a preview payload for UI rendering.",
        )

    async def print_report(self, report_id: str) -> PrintReportResponse:
        return PrintReportResponse(
            message="Report sent to print queue.",
            report_id=report_id,
            print_job_id=f"PRINT-{datetime.now(timezone.utc).strftime('%H%M%S')}",
        )

    async def _db_rows(self) -> list[ReportGenerationRow]:
        stmt = select(LabReportRecord).where(LabReportRecord.hospital_id == self.hospital_id)
        recs = (await self.db.execute(stmt)).scalars().all()
        return [
            ReportGenerationRow(
                report_id=r.report_id,
                patient_name=r.patient_name,
                test_type=r.test_type,
                completion_date=r.completion_date,
                status=r.status,
                verified_by=r.verified_by,
            ) for r in recs
        ]

