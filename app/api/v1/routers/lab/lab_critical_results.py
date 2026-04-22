"""
Critical Results Management endpoints for Lab portal.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_critical_results import (
    CriticalResultsActionResponse,
    CriticalResultsDashboardResponse,
)
from app.services.lab_critical_results_service import LabCriticalResultsService

router = APIRouter(
    prefix="/lab/critical-results",
    tags=["Lab - Critical Results"],
)


@router.get("", response_model=CriticalResultsDashboardResponse)
async def get_critical_results_dashboard(
    for_date: Optional[date] = Query(None),
    demo: bool = Query(
        False,
        description="Return static sample data matching UI cards/table.",
    ),
    search: Optional[str] = Query(
        None,
        description="Filter by patient, test name, or test id.",
    ),
    current_user: User = Depends(
        require_roles(
            [
                "LAB_TECH",
                "LAB_SUPERVISOR",
                "LAB_ADMIN",
                "PATHOLOGIST",
                "HOSPITAL_ADMIN",
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
) -> CriticalResultsDashboardResponse:
    svc = LabCriticalResultsService(db, current_user.hospital_id)
    return await svc.get_dashboard(for_date=for_date, demo=demo, search=search)


@router.post("/{alert_id}/notify", response_model=CriticalResultsActionResponse)
async def start_notification_protocol(
    alert_id: str,
    current_user: User = Depends(
        require_roles(
            [
                "LAB_TECH",
                "LAB_SUPERVISOR",
                "LAB_ADMIN",
                "HOSPITAL_ADMIN",
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
) -> CriticalResultsActionResponse:
    svc = LabCriticalResultsService(db, current_user.hospital_id)
    return await svc.mark_notified(alert_id)

