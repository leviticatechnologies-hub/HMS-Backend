"""
Analytics API - Platform and hospital analytics.
Super Admin: platform-wide dashboard. Hospital Admin: hospital-scoped reports.
"""
from fastapi import APIRouter, Depends, Query
from app.api.deps import get_db_session, require_super_admin
from app.models.user import User
from app.services.super_admin_service import SuperAdminService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/overview")
async def get_analytics_overview(
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Super Admin dashboard overview: total hospitals, active users, revenue (optional in UI).
    Subscriptions breakdown included. No patient/bed KPIs.
    Super Admin only.
    """
    service = SuperAdminService(db)
    return await service.get_platform_analytics()


@router.get("/audit-logs")
async def get_audit_logs(
    resource_type: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """Platform audit logs. Super Admin only."""
    service = SuperAdminService(db)
    return await service.get_platform_audit_logs(skip=skip, limit=limit, resource_type=resource_type)
