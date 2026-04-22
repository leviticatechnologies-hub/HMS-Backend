"""
Report Generation endpoints for Lab portal.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_report_generation import (
    GenerateReportRequest,
    GenerateReportResponse,
    PrintReportResponse,
    ReadyTestsResponse,
    ReportGenerationListResponse,
    ReportPreviewResponse,
)
from app.services.lab_report_generation_service import LabReportGenerationService

router = APIRouter(
    prefix="/lab/report-generation",
    tags=["Lab - Report Generation"],
)


@router.get("", response_model=ReportGenerationListResponse)
async def list_reports(
    demo: bool = Query(False),
    search: Optional[str] = Query(None),
    template: str = Query("STANDARD", description="STANDARD|COMPREHENSIVE|DOCTOR_SUMMARY|PATIENT_FRIENDLY|CUSTOM"),
    current_user: User = Depends(
        require_roles(
            ["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "PATHOLOGIST", "HOSPITAL_ADMIN"]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ReportGenerationListResponse:
    svc = LabReportGenerationService(db, current_user.hospital_id)
    return await svc.list_reports(demo=demo, search=search, template=template)


@router.get("/ready-tests", response_model=ReadyTestsResponse)
async def list_ready_tests(
    demo: bool = Query(False),
    current_user: User = Depends(
        require_roles(
            ["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "PATHOLOGIST", "HOSPITAL_ADMIN"]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ReadyTestsResponse:
    svc = LabReportGenerationService(db, current_user.hospital_id)
    return await svc.ready_tests(demo=demo)


@router.post("/generate", response_model=GenerateReportResponse)
async def generate_report(
    request: GenerateReportRequest,
    current_user: User = Depends(
        require_roles(
            ["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "PATHOLOGIST", "HOSPITAL_ADMIN"]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
) -> GenerateReportResponse:
    svc = LabReportGenerationService(db, current_user.hospital_id)
    return await svc.generate(request)


@router.get("/{report_id}/preview", response_model=ReportPreviewResponse)
async def preview_report(
    report_id: str,
    template: str = Query("STANDARD"),
    current_user: User = Depends(
        require_roles(
            ["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "PATHOLOGIST", "HOSPITAL_ADMIN"]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ReportPreviewResponse:
    svc = LabReportGenerationService(db, current_user.hospital_id)
    return await svc.preview(report_id, template=template)


@router.post("/{report_id}/print", response_model=PrintReportResponse)
async def print_report(
    report_id: str,
    current_user: User = Depends(
        require_roles(
            ["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "PATHOLOGIST", "HOSPITAL_ADMIN"]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
) -> PrintReportResponse:
    svc = LabReportGenerationService(db, current_user.hospital_id)
    return await svc.print_report(report_id)

