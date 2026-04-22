"""
Service for Lab Critical Results Management dashboard.
Current implementation provides demo-ready payloads until result tables are rebuilt.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabCriticalAlert
from app.schemas.lab_critical_results import (
    CriticalAlertRow,
    CriticalComplianceAdvisory,
    CriticalResultsActionResponse,
    CriticalResultsDashboardResponse,
    CriticalResultsMeta,
    CriticalSummary,
    CriticalSummaryCard,
    CriticalUrgentBanner,
)


class LabCriticalResultsService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    def _demo_alerts(self) -> List[CriticalAlertRow]:
        return [
            CriticalAlertRow(
                alert_id="ALRT-001",
                test_id="TEST-2024-012",
                patient_name="Ravi Kumar",
                test_name="Creatinine",
                result_value="4.2 mg/dL",
                alert_level="CRITICAL_HIGH",
                requested_by="Dr. Sharma",
                result_time_label="09:45 AM",
                status="PENDING",
                acknowledged=False,
            ),
            CriticalAlertRow(
                alert_id="ALRT-002",
                test_id="TEST-2024-013",
                patient_name="Sunita Rao",
                test_name="Potassium",
                result_value="6.5 mEq/L",
                alert_level="CRITICAL_HIGH",
                requested_by="Dr. Verma",
                result_time_label="10:15 AM",
                status="PENDING",
                acknowledged=False,
            ),
            CriticalAlertRow(
                alert_id="ALRT-003",
                test_id="TEST-2024-014",
                patient_name="Mohan Singh",
                test_name="Glucose",
                result_value="40 mg/dL",
                alert_level="CRITICAL_LOW",
                requested_by="Dr. Gupta",
                result_time_label="10:30 AM",
                status="NOTIFIED",
                acknowledged=True,
            ),
            CriticalAlertRow(
                alert_id="ALRT-004",
                test_id="TEST-2024-020",
                patient_name="Anjali Devi",
                test_name="Hemoglobin",
                result_value="6.2 g/dL",
                alert_level="CRITICAL_LOW",
                requested_by="Dr. Reddy",
                result_time_label="11:15 AM",
                status="NOTIFIED",
                acknowledged=True,
            ),
            CriticalAlertRow(
                alert_id="ALRT-005",
                test_id="TEST-2024-021",
                patient_name="Vijay Kumar",
                test_name="Troponin I",
                result_value="1.5 ng/mL",
                alert_level="CRITICAL_HIGH",
                requested_by="Dr. Khan",
                result_time_label="12:00 PM",
                status="NOTIFIED",
                acknowledged=True,
            ),
        ]

    async def get_dashboard(
        self,
        *,
        for_date: Optional[date] = None,
        demo: bool = False,
        search: Optional[str] = None,
    ) -> CriticalResultsDashboardResponse:
        d = for_date or datetime.now(timezone.utc).date()
        alerts = self._demo_alerts() if demo else await self._db_alerts()

        if search:
            s = search.strip().lower()
            alerts = [
                a
                for a in alerts
                if s in a.patient_name.lower()
                or s in a.test_name.lower()
                or s in a.test_id.lower()
            ]

        pending = sum(1 for a in alerts if a.status == "PENDING")
        notified = sum(1 for a in alerts if a.status in ("NOTIFIED", "ACKNOWLEDGED"))
        total = len(alerts)

        return CriticalResultsDashboardResponse(
            meta=CriticalResultsMeta(
                generated_at=datetime.now(timezone.utc),
                for_date=d,
                live_data=False,
                demo_data=demo,
            ),
            summary=CriticalSummary(
                pending_notifications=CriticalSummaryCard(
                    value=pending,
                    subtitle="Requires immediate action",
                ),
                successfully_notified=CriticalSummaryCard(
                    value=notified,
                    subtitle="Compliance targets met",
                ),
                total_critical_alerts_24h=CriticalSummaryCard(
                    value=total,
                    subtitle="Updated just now",
                ),
            ),
            urgent_banner=CriticalUrgentBanner(
                show=pending > 0,
                pending_unacknowledged_count=pending,
                message=(
                    f"There are {pending} critical results that have not been acknowledged by physicians. "
                    "Please initiate call-back protocols."
                    if pending
                    else ""
                ),
            ),
            alerts=alerts,
            compliance_advisory=CriticalComplianceAdvisory(
                text="Hospital compliance advisory: maintain notification evidence and call log timestamps.",
                needs_action=pending > 0,
            ),
        )

    async def mark_notified(self, alert_id: str) -> CriticalResultsActionResponse:
        stmt = select(LabCriticalAlert).where(
            LabCriticalAlert.hospital_id == self.hospital_id,
            LabCriticalAlert.alert_id == alert_id,
        )
        rec = (await self.db.execute(stmt)).scalar_one_or_none()
        if rec:
            rec.notify_status = "NOTIFIED"
            rec.acknowledged = "true"
            await self.db.commit()
        return CriticalResultsActionResponse(
            message="Notification protocol started for critical alert.",
            updated_alert_id=alert_id,
            status="NOTIFIED",
        )

    async def _db_alerts(self) -> List[CriticalAlertRow]:
        stmt = (
            select(LabCriticalAlert)
            .where(LabCriticalAlert.hospital_id == self.hospital_id)
            .order_by(LabCriticalAlert.created_at.desc())
        )
        recs = (await self.db.execute(stmt)).scalars().all()
        return [
            CriticalAlertRow(
                alert_id=r.alert_id,
                test_id=r.test_id,
                patient_name=r.patient_name,
                test_name=r.test_name,
                result_value=r.result_value,
                alert_level=r.alert_level,
                requested_by=r.doctor_name or "Doctor",
                result_time_label=r.result_time_label,
                status=r.notify_status,
                acknowledged=(str(r.acknowledged).lower() == "true"),
            )
            for r in recs
        ]

