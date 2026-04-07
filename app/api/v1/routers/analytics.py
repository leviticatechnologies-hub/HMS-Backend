"""
Analytics API - Platform and hospital analytics.
Super Admin: platform-wide dashboard, reports & monitoring. Hospital Admin: hospital-scoped reports.
"""
from fastapi import APIRouter, Depends, Query
from app.api.deps import get_db_session, require_super_admin
from app.models.user import User
from app.services.super_admin_service import SuperAdminService
from app.services.reports_analytics_service import ReportsAnalyticsService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/overview")
async def get_analytics_overview(
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Super Admin dashboard overview: appointments, beds, billing, doctors; subscriptions.
    Subscriptions breakdown included. No patient/bed KPIs.
    Super Admin only.
    """
    service = SuperAdminService(db)
    return await service.get_platform_analytics()


@router.get("/reports/system-monitoring")
async def get_reports_system_monitoring(
    days: int = Query(7, ge=1, le=90, description="Window for activity & payment error proxy"),
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """
    **Reports §10.1** — System monitoring (Super Admin):
    active users, activity proxy from audit logs, payment failure rate proxy.
    """
    svc = ReportsAnalyticsService(db)
    return await svc.get_system_monitoring(days=days)


@router.get("/reports/business")
async def get_reports_business_analytics(
    revenue_days: int = Query(90, ge=7, le=730, description="Revenue trend window"),
    hospital_growth_months: int = Query(
        12, ge=1, le=60, description="Months of hospital signup history"
    ),
    current_user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db_session),
):
    """
    **Reports §10.1** — Business analytics (Super Admin):
    revenue trends, hospital growth by month, feature/module adoption from subscription plans.
    """
    svc = ReportsAnalyticsService(db)
    return await svc.get_business_analytics(
        revenue_days=revenue_days,
        hospital_growth_months=hospital_growth_months,
    )


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
