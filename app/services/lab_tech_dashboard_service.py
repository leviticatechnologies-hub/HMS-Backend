"""
Aggregates data for the Lab Technician dashboard.
Uses live `lab_equipment` / maintenance; test & QC order metrics are placeholders until tables exist.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab import Equipment
from app.schemas.lab_tech_dashboard import LabTechDashboardResponse


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _ui_equipment_status(eq: Equipment) -> Tuple[str, str]:
    """
    Map DB row to UI badge: operational | maintenance | calibration_due | inactive
    """
    st = (eq.status or "").upper()
    if st in ("UNDER_MAINTENANCE", "DOWN"):
        return "maintenance", st.replace("_", " ").title()
    if st == "INACTIVE" or not eq.is_active:
        return "inactive", "Inactive"
    nxt = eq.next_calibration_due_at
    if nxt is not None:
        nxt_utc = nxt if nxt.tzinfo else nxt.replace(tzinfo=timezone.utc)
        if nxt_utc.date() <= _utc_today() + timedelta(days=7):
            return "calibration_due", "Calibration due"
    if st == "ACTIVE":
        return "operational", "Operational"
    return "operational", st or "Unknown"


class LabTechDashboardService:
    def __init__(self, db: AsyncSession, hospital_id: uuid.UUID):
        self.db = db
        self.hospital_id = hospital_id

    async def _load_equipment(self) -> List[Equipment]:
        res = await self.db.execute(
            select(Equipment)
            .where(Equipment.hospital_id == self.hospital_id)
            .order_by(Equipment.equipment_code)
        )
        return list(res.scalars().all())

    async def get_dashboard(
        self,
        *,
        for_date: Optional[date] = None,
        demo: bool = False,
    ) -> LabTechDashboardResponse:
        equipment = await self._load_equipment()
        d = for_date or _utc_today()
        return self._build(equipment, for_date=d, demo=demo)

    def _build(
        self,
        equipment: List[Equipment],
        *,
        for_date: date,
        demo: bool,
    ) -> LabTechDashboardResponse:
        from app.schemas.lab_tech_dashboard import (
            CategorySliceModel,
            DashboardTableRowCritical,
            DashboardTableRowPending,
            EquipmentPointModel,
            EquipmentStatusRow,
            KpiCardModel,
            KpiStripModel,
            LabAlertItem,
            LabTechDashboardMeta,
            QcStatusTodayRow,
            QcTrendPanelModel,
            QcTrendPointModel,
            StackedTimeSeriesModel,
            TestsByStatusBarModel,
            WeeklyDayPointModel,
        )

        meta = LabTechDashboardMeta(
            tests_metrics_available=False,
            qc_metrics_available=False,
            demo_data=demo,
            generated_at=datetime.now(timezone.utc),
            for_date=for_date,
        )

        if demo:
            kpis = KpiStripModel(
                total_tests=KpiCardModel(
                    value=156,
                    subtitle="tests processed today",
                    trend_percent=12.0,
                ),
                pending_tests=KpiCardModel(
                    value=24,
                    subtitle="awaiting processing",
                ),
                completed_tests=KpiCardModel(
                    value=132,
                    subtitle="reports generated",
                    completion_rate_percent=85.0,
                ),
                critical_results=KpiCardModel(
                    value=3,
                    subtitle="needs immediate review",
                ),
            )
            alerts = [
                LabAlertItem(
                    id="1",
                    severity="warning",
                    code="EQUIPMENT_CALIBRATION",
                    message="Chemistry Analyzer requires calibration",
                ),
                LabAlertItem(
                    id="2",
                    severity="warning",
                    code="QC_FAIL",
                    message="QC failed for Creatinine test",
                ),
                LabAlertItem(
                    id="3",
                    severity="critical",
                    code="CRITICAL_PENDING",
                    message="3 critical results pending physician notification",
                ),
            ]
            test_volume = StackedTimeSeriesModel(
                title="Test Volume Over Time",
                labels=["8", "9", "10", "11", "12", "13", "14", "15", "16", "17"],
                series={
                    "tests_received": [8, 12, 15, 18, 14, 20, 22, 19, 16, 10],
                    "tests_completed": [6, 10, 14, 16, 12, 18, 20, 17, 14, 8],
                },
            )
            categories = [
                CategorySliceModel(name="Hematology", count=45, percent=28.8),
                CategorySliceModel(name="Biochemistry", count=52, percent=33.3),
                CategorySliceModel(name="Microbiology", count=30, percent=19.2),
                CategorySliceModel(name="Serology", count=29, percent=18.6),
            ]
            by_status = TestsByStatusBarModel(
                title="Tests by Status",
                subtitle="Current processing status",
                labels=[
                    "Sample Collection",
                    "Sample Processing",
                    "Testing",
                    "Culture In Progress",
                    "Analysis",
                    "Reporting",
                ],
                values=[6, 8, 5, 3, 1, 1],
            )
            qc_trend = QcTrendPanelModel(
                test_name="Glucose QC",
                min_acceptable=95.0,
                max_acceptable=105.0,
                points=[
                    QcTrendPointModel(t="8 AM", value=100.0, in_range=True),
                    QcTrendPointModel(t="9 AM", value=98.0, in_range=True),
                    QcTrendPointModel(t="10 AM", value=104.0, in_range=True),
                    QcTrendPointModel(t="11 AM", value=97.0, in_range=True),
                    QcTrendPointModel(t="12 PM", value=99.0, in_range=True),
                ],
                within_range=5,
                warnings=2,
                failures=0,
            )
            eq_perf = [
                EquipmentPointModel(
                    equipment_label="Analyzer A", efficiency_percent=97.0, downtime_hours=1.0
                ),
                EquipmentPointModel(
                    equipment_label="Analyzer B", efficiency_percent=91.0, downtime_hours=3.0
                ),
                EquipmentPointModel(
                    equipment_label="Centrifuge 2", efficiency_percent=88.0, downtime_hours=4.0
                ),
                EquipmentPointModel(
                    equipment_label="Incubator", efficiency_percent=99.0, downtime_hours=0.5
                ),
            ]
            weekly = [
                WeeklyDayPointModel(day_label="Mon", total_tests=140, critical_results=2),
                WeeklyDayPointModel(day_label="Tue", total_tests=152, critical_results=1),
                WeeklyDayPointModel(day_label="Wed", total_tests=148, critical_results=3),
                WeeklyDayPointModel(day_label="Thu", total_tests=160, critical_results=2),
                WeeklyDayPointModel(day_label="Fri", total_tests=155, critical_results=1),
                WeeklyDayPointModel(day_label="Sat", total_tests=90, critical_results=0),
            ]
            pending_rows = [
                DashboardTableRowPending(
                    test_id="LT-2401",
                    patient_name="R. Kumar",
                    test_name="CBC + Diff",
                    status_or_priority="High",
                ),
            ]
            crit_rows = [
                DashboardTableRowCritical(
                    test_id="LT-2388",
                    patient_name="M. Sen",
                    test_name="Potassium",
                    value="6.1 mmol/L",
                ),
            ]
            qc_today = [
                QcStatusTodayRow(
                    test_name="CBC",
                    status="Passed",
                    value="12.5",
                    target="12.0±0.5",
                ),
                QcStatusTodayRow(
                    test_name="Glucose",
                    status="Warning",
                    value="105",
                    target="100±5",
                ),
                QcStatusTodayRow(
                    test_name="Creatinine",
                    status="Failed",
                    value="2.5",
                    target="1.8±0.2",
                ),
            ]
        else:
            kpis = KpiStripModel(
                total_tests=KpiCardModel(
                    value=0,
                    subtitle="tests processed today (no test pipeline table yet)",
                ),
                pending_tests=KpiCardModel(value=0, subtitle="awaiting processing"),
                completed_tests=KpiCardModel(
                    value=0, subtitle="reports generated", completion_rate_percent=None
                ),
                critical_results=KpiCardModel(
                    value=0, subtitle="needs immediate review"
                ),
            )
            alerts: List[LabAlertItem] = []
            for eq in equipment:
                ui, detail = _ui_equipment_status(eq)
                nxt = eq.next_calibration_due_at
                if nxt and ui == "calibration_due":
                    alerts.append(
                        LabAlertItem(
                            id=f"cal-{eq.id}",
                            severity="warning",
                            code="EQUIPMENT_CALIBRATION",
                            message=f"{eq.name} requires calibration or review ({detail})",
                            related_equipment_id=eq.id,
                        )
                    )
            test_volume = StackedTimeSeriesModel(
                title="Test Volume Over Time",
                labels=[],
                series={},
            )
            categories = []
            by_status = TestsByStatusBarModel()
            qc_trend = QcTrendPanelModel()
            if equipment:
                eq_perf = [
                    EquipmentPointModel(
                        equipment_label=eq.name[:32],
                        efficiency_percent=_demo_efficiency(eq),
                        downtime_hours=_demo_downtime(eq),
                    )
                    for eq in equipment
                ][:6]
            else:
                eq_perf = []
            weekly = []
            pending_rows = []
            crit_rows = []
            qc_today = []

        # Equipment table — always from DB, merged with UI mapping
        eq_status_rows: List[EquipmentStatusRow] = []
        for eq in equipment:
            ui, detail = _ui_equipment_status(eq)
            eq_status_rows.append(
                EquipmentStatusRow(
                    equipment_id=eq.id,
                    equipment_code=eq.equipment_code,
                    equipment_name=eq.name,
                    ui_status=ui,
                    status_detail=detail,
                    db_status=eq.status or "",
                )
            )

        # If not demo, QC table can mirror empty; demo already set qc_today

        return LabTechDashboardResponse(
            meta=meta,
            kpis=kpis,
            alerts=alerts,
            test_volume_today=test_volume,
            test_categories=categories,
            tests_by_workflow_status=by_status,
            qc_trend=qc_trend,
            equipment_performance=eq_perf,
            weekly_test_trends=weekly,
            weekly_avg_tests_per_day=145.0 if demo else None,
            weekly_change_percent=8.0 if demo else None,
            pending_tests_table=pending_rows,
            critical_results_table=crit_rows,
            equipment_status=eq_status_rows,
            qc_status_today=qc_today,
        )


def _demo_efficiency(eq: Equipment) -> float:
    """Rough visual default from status."""
    s = (eq.status or "").upper()
    if s == "ACTIVE":
        return 94.0
    if s == "UNDER_MAINTENANCE":
        return 72.0
    if s == "DOWN":
        return 40.0
    return 85.0


def _demo_downtime(eq: Equipment) -> float:
    s = (eq.status or "").upper()
    if s == "ACTIVE":
        return 1.2
    if s == "UNDER_MAINTENANCE":
        return 4.0
    if s == "DOWN":
        return 7.0
    return 2.0
