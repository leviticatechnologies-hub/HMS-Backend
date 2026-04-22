"""
Equipment Tracking endpoints.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_equipment_tracking import (
    AddEquipmentTrackingRequest,
    AddEquipmentTrackingResponse,
    EquipmentTrackingActionResponse,
    EquipmentTrackingDashboardResponse,
)
from app.services.lab_equipment_tracking_service import LabEquipmentTrackingService
from app.services.lab_service import LabService

router = APIRouter(prefix="/lab/equipment-tracking", tags=["Lab - Equipment Tracking"])


@router.get("", response_model=EquipmentTrackingDashboardResponse)
async def get_equipment_tracking(
    demo: bool = Query(False),
    search: Optional[str] = Query(None),
    current_user: User = Depends(
        require_roles(["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "PATHOLOGIST", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> EquipmentTrackingDashboardResponse:
    lab = LabService(db, current_user.hospital_id)
    svc = LabEquipmentTrackingService(lab)
    return await svc.dashboard(demo=demo, search=search)


@router.post("/equipment", response_model=AddEquipmentTrackingResponse)
async def add_equipment_tracking(
    request: AddEquipmentTrackingRequest,
    current_user: User = Depends(
        require_roles(["LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> AddEquipmentTrackingResponse:
    lab = LabService(db, current_user.hospital_id)
    svc = LabEquipmentTrackingService(lab)
    return await svc.add_equipment(request)


@router.post("/quick-action/{action}", response_model=EquipmentTrackingActionResponse)
async def run_equipment_quick_action(
    action: str,
    _current_user: User = Depends(
        require_roles(["LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
) -> EquipmentTrackingActionResponse:
    return EquipmentTrackingActionResponse(message=f"{action} initiated.", action=action)

