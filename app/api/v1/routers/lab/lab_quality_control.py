"""
Quality Control Workflows endpoints.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.lab.rbac import LAB_GET_ROLES
from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_quality_control import (
    QcWorkflowActionResponse,
    QualityControlDashboardResponse,
    RecordQcRunRequest,
    RecordQcRunResponse,
)
from app.services.lab_quality_control_service import LabQualityControlService

router = APIRouter(prefix="/lab/quality-control", tags=["Lab - Quality Control"])


@router.get("", response_model=QualityControlDashboardResponse)
async def get_quality_control_dashboard(
    demo: bool = Query(False),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> QualityControlDashboardResponse:
    svc = LabQualityControlService(db, current_user.hospital_id)
    return await svc.dashboard(demo=demo)


@router.post("/run", response_model=RecordQcRunResponse)
async def record_qc_run(
    request: RecordQcRunRequest,
    current_user: User = Depends(
        require_roles(["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> RecordQcRunResponse:
    svc = LabQualityControlService(db, current_user.hospital_id)
    return await svc.record_qc_run(request)


@router.post("/workflow/{action}", response_model=QcWorkflowActionResponse)
async def trigger_qc_workflow_action(
    action: str,
    current_user: User = Depends(
        require_roles(["LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> QcWorkflowActionResponse:
    svc = LabQualityControlService(db, current_user.hospital_id)
    return await svc.workflow_action(action)

