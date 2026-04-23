"""
Test Registration endpoints for Lab portal UI.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.lab.rbac import LAB_GET_ROLES
from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_test_registration import (
    RegisterTestRequest,
    RegisterTestResponse,
    TestRegistrationListResponse,
)
from app.services.lab_test_registration_service import LabTestRegistrationService

router = APIRouter(
    prefix="/lab/test-registration",
    tags=["Lab - Test Registration"],
)


@router.get("", response_model=TestRegistrationListResponse)
async def list_test_registrations(
    for_date: Optional[date] = Query(None),
    demo: bool = Query(
        False,
        description="Return static sample rows matching UI.",
    ),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="SAMPLE_PENDING|SAMPLE_COLLECTED|IN_PROGRESS|COMPLETED"),
    priority: Optional[str] = Query(None, description="URGENT|ROUTINE"),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> TestRegistrationListResponse:
    svc = LabTestRegistrationService(db, current_user.hospital_id)
    return await svc.list_tests(
        for_date=for_date,
        demo=demo,
        search=search,
        status=status,
        priority=priority,
    )


@router.post("", response_model=RegisterTestResponse)
async def register_new_test(
    request: RegisterTestRequest,
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
) -> RegisterTestResponse:
    svc = LabTestRegistrationService(db, current_user.hospital_id)
    return await svc.register_test(request)

