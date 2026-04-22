"""
Response models for the Lab Technician dashboard (Levitica-style UI).
Test/QC order metrics are empty or demo-filled until the full lab order stack exists.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KpiCardModel(BaseModel):
    """KPI card (Total tests, Pending, Completed, Critical)."""

    value: int = 0
    subtitle: str = ""
    trend_percent: Optional[float] = None  # e.g. 12.0 for +12%
    trend_label: str = "vs prior period"
    # Completion rate for "Completed" card (0–100)
    completion_rate_percent: Optional[float] = None


class KpiStripModel(BaseModel):
    total_tests: KpiCardModel
    pending_tests: KpiCardModel
    completed_tests: KpiCardModel
    critical_results: KpiCardModel


class LabAlertItem(BaseModel):
    """Top banner / alert line."""

    id: str
    severity: str = Field(
        description="info | warning | critical",
    )
    code: str = Field(
        description="EQUIPMENT_CALIBRATION | QC_FAIL | CRITICAL_PENDING | custom",
    )
    message: str
    link: Optional[str] = None
    related_equipment_id: Optional[UUID] = None


class NamedSeriesPoint(BaseModel):
    label: str
    value: float


class StackedTimeSeriesModel(BaseModel):
    """e.g. Test volume: received vs completed by hour."""

    title: str
    labels: List[str] = Field(default_factory=list)
    series: Dict[str, List[float]] = Field(
        default_factory=dict,
        description="Keys: tests_received, tests_completed, etc.",
    )


class CategorySliceModel(BaseModel):
    name: str
    count: int
    percent: Optional[float] = None


class TestsByStatusBarModel(BaseModel):
    """Bar chart: Sample Collection, Processing, ..."""

    title: str = "Tests by Status"
    subtitle: str = "Current processing status"
    labels: List[str] = Field(default_factory=list)
    values: List[int] = Field(default_factory=list)


class QcTrendPointModel(BaseModel):
    t: str  # label e.g. "8 AM" or ISO time
    value: float
    in_range: bool = True


class QcTrendPanelModel(BaseModel):
    test_name: str = "—"
    unit: str = "mg/dL"
    min_acceptable: Optional[float] = None
    max_acceptable: Optional[float] = None
    points: List[QcTrendPointModel] = Field(default_factory=list)
    within_range: int = 0
    warnings: int = 0
    failures: int = 0


class EquipmentPointModel(BaseModel):
    """One equipment name on X-axis (efficiency vs downtime)."""

    equipment_label: str
    efficiency_percent: float
    downtime_hours: float


class WeeklyDayPointModel(BaseModel):
    day_label: str
    total_tests: int
    critical_results: int


class DashboardTableRowPending(BaseModel):
    test_id: str
    patient_name: str
    test_name: str
    status_or_priority: str = ""


class DashboardTableRowCritical(BaseModel):
    test_id: str
    patient_name: str
    test_name: str
    value: str = ""


class EquipmentStatusRow(BaseModel):
    """Equipment table on dashboard (matches UI: EQP-xxx, name, badge)."""

    equipment_id: UUID
    equipment_code: str
    equipment_name: str
    # operational | maintenance | calibration_due | inactive
    ui_status: str
    status_detail: str = ""
    # Original DB status for power users
    db_status: str = ""


class QcStatusTodayRow(BaseModel):
    """QC status table — populated when QC module exists; else empty or demo."""

    test_name: str
    status: str  # Passed | Warning | Failed
    value: str
    target: str


class LabTechDashboardMeta(BaseModel):
    """Explains which sections use live DB vs placeholders."""

    tests_metrics_available: bool = False
    qc_metrics_available: bool = False
    demo_data: bool = False
    generated_at: datetime
    for_date: date


class LabTechDashboardResponse(BaseModel):
    """Full lab technician dashboard payload (single GET)."""

    meta: LabTechDashboardMeta
    kpis: KpiStripModel
    alerts: List[LabAlertItem] = Field(default_factory=list)
    test_volume_today: StackedTimeSeriesModel
    test_categories: List[CategorySliceModel] = Field(default_factory=list)
    tests_by_workflow_status: TestsByStatusBarModel
    qc_trend: QcTrendPanelModel
    equipment_performance: List[EquipmentPointModel] = Field(default_factory=list)
    weekly_test_trends: List[WeeklyDayPointModel] = Field(default_factory=list)
    weekly_avg_tests_per_day: Optional[float] = None
    weekly_change_percent: Optional[float] = None
    pending_tests_table: List[DashboardTableRowPending] = Field(default_factory=list)
    critical_results_table: List[DashboardTableRowCritical] = Field(default_factory=list)
    equipment_status: List[EquipmentStatusRow] = Field(default_factory=list)
    qc_status_today: List[QcStatusTodayRow] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
