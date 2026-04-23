"""
Secure Result Access endpoints for lab portal.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.lab.rbac import LAB_GET_ROLES
from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_result_access import (
    GrantResultAccessRequest,
    GrantResultAccessResponse,
    ResultAccessDashboardResponse,
)
from app.services.lab_result_access_service import LabResultAccessService

router = APIRouter(
    prefix="/lab/result-access",
    tags=["Lab - Result Access"],
)


@router.get("", response_model=ResultAccessDashboardResponse)
async def get_result_access_dashboard(
    demo: bool = Query(False, description="Return static UI-aligned sample data."),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="ACTIVE|EXPIRED|REVOKED"),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> ResultAccessDashboardResponse:
    svc = LabResultAccessService(db, current_user.hospital_id)
    return await svc.get_dashboard(demo=demo, search=search, status=status)


@router.post("/grant", response_model=GrantResultAccessResponse)
async def grant_result_access(
    request: GrantResultAccessRequest,
    current_user: User = Depends(
        require_roles(
            ["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
) -> GrantResultAccessResponse:
    svc = LabResultAccessService(db, current_user.hospital_id)
    return await svc.grant_access(request)

