"""
Test Catalogue Management endpoints.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_test_catalogue import (
    AddCategoryRequest,
    AddCategoryResponse,
    AddTestRequest,
    AddTestResponse,
    BulkActionResponse,
    TestCatalogueListResponse,
)
from app.services.lab_test_catalogue_service import LabTestCatalogueService

router = APIRouter(
    prefix="/lab/test-catalogue",
    tags=["Lab - Test Catalogue"],
)


@router.get("", response_model=TestCatalogueListResponse)
async def list_test_catalogue(
    demo: bool = Query(False),
    search: Optional[str] = Query(None, description="Search tests by code or name"),
    category: Optional[str] = Query(None, description="Filter by category chip label"),
    current_user: User = Depends(
        require_roles(["LAB_TECH", "LAB_SUPERVISOR", "LAB_ADMIN", "PATHOLOGIST", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> TestCatalogueListResponse:
    svc = LabTestCatalogueService(db, current_user.hospital_id)
    return await svc.list_catalogue(demo=demo, search=search, category=category)


@router.post("/category", response_model=AddCategoryResponse)
async def add_test_category(
    request: AddCategoryRequest,
    current_user: User = Depends(
        require_roles(["LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> AddCategoryResponse:
    svc = LabTestCatalogueService(db, current_user.hospital_id)
    return await svc.add_category(request)


@router.post("/test", response_model=AddTestResponse)
async def add_catalogue_test(
    request: AddTestRequest,
    current_user: User = Depends(
        require_roles(["LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> AddTestResponse:
    svc = LabTestCatalogueService(db, current_user.hospital_id)
    return await svc.add_test(request)


@router.post("/bulk/{action}", response_model=BulkActionResponse)
async def run_catalogue_bulk_action(
    action: str,
    current_user: User = Depends(
        require_roles(["LAB_SUPERVISOR", "LAB_ADMIN", "HOSPITAL_ADMIN"])
    ),
    db: AsyncSession = Depends(get_db_session),
) -> BulkActionResponse:
    svc = LabTestCatalogueService(db, current_user.hospital_id)
    return await svc.bulk_action(action)

