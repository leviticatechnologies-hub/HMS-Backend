"""
Reports & Analytics — System monitoring and business KPIs (Super Admin).

Data sources: users, hospitals, subscriptions, payments, audit_logs.
Assumes platform DB holds aggregates (same as `get_platform_analytics`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SubscriptionStatus, UserStatus
from app.core.plan_features import DEFAULT_FEATURES_BY_PLAN, FEATURE_PHARMACY, FEATURE_VIDEO_CONSULTATION, normalize_plan_name
from app.models.billing import BillingPayment
from app.models.tenant import Hospital, HospitalSubscription, SubscriptionPlanModel
from app.models.user import AuditLog, User


class ReportsAnalyticsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_system_monitoring(self, days: int = 7) -> Dict[str, Any]:
        """
        System monitoring snapshot:
        - Active users (accounts + recent logins)
        - API / activity proxy (audit log volume — not raw HTTP; use APM for true API metrics)
        - Error-rate proxy (failed payments vs succeeded + failed)
        """
        days = max(1, min(days, 90))
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        since_24h = now - timedelta(hours=24)

        active_users_q = await self.db.execute(
            select(func.count(User.id)).where(
                User.is_active.is_(True),
                User.status == UserStatus.ACTIVE.value,
            )
        )
        active_users_total = active_users_q.scalar() or 0

        recent_login_q = await self.db.execute(
            select(func.count(User.id)).where(
                User.is_active.is_(True),
                User.last_login.isnot(None),
                User.last_login >= since_24h,
            )
        )
        users_active_last_24h = recent_login_q.scalar() or 0

        # Activity proxy: audit log writes per day (all hospitals visible in this DB)
        trunc_day = func.date_trunc("day", AuditLog.created_at).label("day")
        activity_q = await self.db.execute(
            select(trunc_day, func.count(AuditLog.id))
            .where(AuditLog.created_at >= since)
            .group_by(trunc_day)
            .order_by(trunc_day)
        )
        activity_by_day: List[Dict[str, Any]] = []
        for row in activity_q.all():
            d, cnt = row[0], int(row[1])
            activity_by_day.append(
                {"date": d.date().isoformat() if d else None, "audit_events": cnt}
            )

        pay_ok = await self.db.execute(
            select(func.count(BillingPayment.id)).where(
                BillingPayment.status == "SUCCESS",
                BillingPayment.created_at >= since,
            )
        )
        pay_fail = await self.db.execute(
            select(func.count(BillingPayment.id)).where(
                BillingPayment.status == "FAILED",
                BillingPayment.created_at >= since,
            )
        )
        success_n = pay_ok.scalar() or 0
        failed_n = pay_fail.scalar() or 0
        denom = success_n + failed_n
        payment_failure_rate = round((failed_n / denom * 100), 2) if denom else 0.0

        return {
            "period_days": days,
            "active_users": {
                "total_active_accounts": active_users_total,
                "with_login_last_24h": users_active_last_24h,
            },
            "api_usage_note": "Audit-event counts proxy platform activity; wire Datadog/New Relic for true HTTP metrics.",
            "activity_proxy_audit_events_by_day": activity_by_day,
            "error_rate_proxy": {
                "payment_attempts_success": success_n,
                "payment_attempts_failed": failed_n,
                "failed_payment_percent": payment_failure_rate,
                "note": "Proxy for operational errors on payments; expand with app error index if added.",
            },
        }

    async def get_business_analytics(
        self,
        revenue_days: int = 90,
        hospital_growth_months: int = 12,
    ) -> Dict[str, Any]:
        """Revenue trends, hospital signups, plan / feature adoption (from subscription data)."""
        revenue_days = max(7, min(revenue_days, 730))
        hospital_growth_months = max(1, min(hospital_growth_months, 60))
        now = datetime.now(timezone.utc)
        rev_since = now - timedelta(days=revenue_days)
        hosp_since = now - timedelta(days=30 * hospital_growth_months)

        trunc_day = func.date_trunc("day", BillingPayment.paid_at).label("day")
        rev_q = await self.db.execute(
            select(trunc_day, func.coalesce(func.sum(BillingPayment.amount), 0))
            .where(
                BillingPayment.status == "SUCCESS",
                BillingPayment.paid_at.isnot(None),
                BillingPayment.paid_at >= rev_since,
            )
            .group_by(trunc_day)
            .order_by(trunc_day)
        )
        revenue_by_day: List[Dict[str, Any]] = []
        for row in rev_q.all():
            d, amt = row[0], float(row[1] or 0)
            revenue_by_day.append({"date": d.date().isoformat() if d else None, "amount": amt})

        trunc_m = func.date_trunc("month", Hospital.created_at).label("month")
        growth_q = await self.db.execute(
            select(trunc_m, func.count(Hospital.id))
            .where(Hospital.created_at >= hosp_since)
            .group_by(trunc_m)
            .order_by(trunc_m)
        )
        hospitals_by_month: List[Dict[str, Any]] = []
        for row in growth_q.all():
            m, cnt = row[0], int(row[1])
            hospitals_by_month.append({"month": m.date().isoformat()[:7] if m else None, "new_hospitals": cnt})

        by_plan = await self.db.execute(
            select(SubscriptionPlanModel.name, func.count(HospitalSubscription.id))
            .join(HospitalSubscription, HospitalSubscription.plan_id == SubscriptionPlanModel.id)
            .where(HospitalSubscription.status == SubscriptionStatus.ACTIVE)
            .group_by(SubscriptionPlanModel.name)
        )
        hospitals_per_plan = {r[0]: r[1] for r in by_plan.all()}

        plan_counts = await self.db.execute(
            select(SubscriptionPlanModel, func.count(HospitalSubscription.id))
            .join(HospitalSubscription, HospitalSubscription.plan_id == SubscriptionPlanModel.id)
            .where(HospitalSubscription.status == SubscriptionStatus.ACTIVE)
            .group_by(SubscriptionPlanModel.id)
        )
        feat_counts = {"lab_tests": 0, "video_consultation": 0, "pharmacy": 0}
        for row in plan_counts.all():
            p, n = row[0], int(row[1])
            if n <= 0:
                continue
            pname = normalize_plan_name(p.name)
            defaults = dict(DEFAULT_FEATURES_BY_PLAN.get(pname, DEFAULT_FEATURES_BY_PLAN["STANDARD"]))
            ov = p.features if isinstance(p.features, dict) else {}
            merged = dict(defaults)
            for key in feat_counts:
                if key in ov:
                    merged[key] = bool(ov[key])
                if merged.get(key):
                    feat_counts[key] += n

        return {
            "revenue_trends": {
                "period_days": revenue_days,
                "success_payments_by_day": revenue_by_day,
            },
            "hospital_growth": {
                "new_hospitals_by_month": hospitals_by_month,
            },
            "feature_usage": {
                "active_subscriptions_by_plan": hospitals_per_plan,
                "hospitals_with_module_enabled_estimate": feat_counts,
                "description": "Counts active subscriptions whose plan defaults/overrides enable each module.",
            },
        }
