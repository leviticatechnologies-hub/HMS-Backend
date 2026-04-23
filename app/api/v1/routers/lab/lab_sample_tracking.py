"""
Sample Tracking endpoints (barcode/QR workflow + quick status actions).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.lab.rbac import LAB_GET_ROLES
from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_sample_tracking import (
    BarcodeLookupResponse,
    SampleActionRequest,
    SampleActionResponse,
    SampleTrackingListResponse,
)
from app.services.lab_sample_tracking_service import LabSampleTrackingService

router = APIRouter(
    prefix="/lab/sample-tracking",
    tags=["Lab - Sample Tracking"],
)


@router.get("", response_model=SampleTrackingListResponse)
async def list_sample_tracking(
    demo: bool = Query(False, description="Return static UI-matching sample rows."),
    search: Optional[str] = Query(None, description="Search by barcode, patient name, or test id."),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> SampleTrackingListResponse:
    svc = LabSampleTrackingService(db, current_user.hospital_id)
    return await svc.list_samples(demo=demo, search=search)


@router.get("/lookup", response_model=BarcodeLookupResponse)
async def lookup_sample_barcode(
    barcode: str = Query(..., description="Barcode (e.g. BC001)"),
    demo: bool = Query(False, description="Lookup against static sample set for UI."),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> BarcodeLookupResponse:
    svc = LabSampleTrackingService(db, current_user.hospital_id)
    return await svc.lookup_barcode(barcode, demo=demo)


@router.post("/simulate-scan", response_model=BarcodeLookupResponse)
async def simulate_scan(
    barcode: str = Query("BC001", description="Simulated barcode to return"),
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
) -> BarcodeLookupResponse:
    """
    UI helper for the scan modal's "Simulate Scan" button.
    Uses the demo set intentionally.
    """
    svc = LabSampleTrackingService(db, current_user.hospital_id)
    return await svc.lookup_barcode(barcode, demo=True)


@router.post("/action", response_model=SampleActionResponse)
async def apply_sample_status_action(
    request: SampleActionRequest,
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
) -> SampleActionResponse:
    svc = LabSampleTrackingService(db, current_user.hospital_id)
    return await svc.apply_action(request)

