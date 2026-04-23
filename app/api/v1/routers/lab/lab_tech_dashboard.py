"""
Lab Technician dashboard — single payload for the Levitica-style lab home UI.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.lab.rbac import LAB_GET_ROLES
from app.database.session import get_db_session
from app.models.user import User
from app.core.security import require_roles
from app.schemas.lab_tech_dashboard import LabTechDashboardResponse
from app.services.lab_tech_dashboard_service import LabTechDashboardService

router = APIRouter(
    prefix="/lab/tech-dashboard",
    tags=["Lab - Technician Dashboard"],
)


@router.get(
    "",
    response_model=LabTechDashboardResponse,
    summary="Lab technician dashboard (full payload)",
    description=(
        "Returns KPIs, charts, equipment status, and tables in one response. "
        "Test and QC order metrics are zero/empty until the test pipeline is wired; "
        "use `demo=true` for sample numbers matching the UI mock."
    ),
)
async def get_lab_tech_dashboard(
    for_date: Optional[date] = Query(
        None,
        description="Reporting date (hospital day); defaults to current UTC date.",
    ),
    demo: bool = Query(
        False,
        description="If true, fill KPI/chart QC/test tables with static demo data for frontend development.",
    ),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> LabTechDashboardResponse:
    svc = LabTechDashboardService(db, current_user.hospital_id)
    return await svc.get_dashboard(for_date=for_date, demo=demo)
