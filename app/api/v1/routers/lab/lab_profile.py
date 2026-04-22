"""
Lab Profile endpoints.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_profile import (
    ChangePasswordResponse,
    ConfigureLabSettingsRequest,
    ConfigureLabSettingsResponse,
    EditLabProfileRequest,
    EditLabProfileResponse,
    LabProfileActionResponse,
    LabProfileResponse,
)
from app.services.lab_profile_service import LabProfileService

router = APIRouter(prefix="/lab/profile", tags=["Lab - Profile"])


@router.get("", response_model=LabProfileResponse)
async def get_lab_profile(
    demo: bool = Query(False),
    current_user: User = Depends(
        require_roles(["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "PATHOLOGIST", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> LabProfileResponse:
    return await LabProfileService(db, current_user.hospital_id).get_profile(demo=demo)


@router.post("/edit", response_model=EditLabProfileResponse)
async def edit_lab_profile(
    request: EditLabProfileRequest,
    current_user: User = Depends(
        require_roles(["LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> EditLabProfileResponse:
    return await LabProfileService(db, current_user.hospital_id).edit_profile(request)


@router.post("/configure-settings", response_model=ConfigureLabSettingsResponse)
async def configure_lab_settings(
    request: ConfigureLabSettingsRequest,
    current_user: User = Depends(
        require_roles(["LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ConfigureLabSettingsResponse:
    return await LabProfileService(db, current_user.hospital_id).configure_settings(request)


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_lab_profile_password(
    current_user: User = Depends(
        require_roles(["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ChangePasswordResponse:
    return await LabProfileService(db, current_user.hospital_id).change_password()


@router.post("/action/{action}", response_model=LabProfileActionResponse)
async def run_lab_profile_action(
    action: str,
    current_user: User = Depends(
        require_roles(["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> LabProfileActionResponse:
    return await LabProfileService(db, current_user.hospital_id).utility_action(action)

