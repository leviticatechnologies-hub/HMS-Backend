"""
Schemas for Lab Critical Results Management UI.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


AlertLevel = Literal["CRITICAL_HIGH", "CRITICAL_LOW", "WARNING"]
NotifyStatus = Literal["PENDING", "NOTIFIED", "ACKNOWLEDGED"]


class CriticalSummaryCard(BaseModel):
    value: int
    subtitle: str


class CriticalSummary(BaseModel):
    pending_notifications: CriticalSummaryCard
    successfully_notified: CriticalSummaryCard
    total_critical_alerts_24h: CriticalSummaryCard


class CriticalUrgentBanner(BaseModel):
    show: bool = False
    pending_unacknowledged_count: int = 0
    message: str = ""
    cta_label: str = "Start Notification Protocol"


class CriticalAlertRow(BaseModel):
    alert_id: str
    test_id: str
    patient_name: str
    test_name: str
    result_value: str
    alert_level: AlertLevel
    requested_by: str
    result_time_label: str
    status: NotifyStatus
    acknowledged: bool = False


class CriticalComplianceAdvisory(BaseModel):
    text: str = "Document physician notification timestamps for medico-legal compliance."
    needs_action: bool = False


class CriticalResultsMeta(BaseModel):
    generated_at: datetime
    for_date: date
    live_data: bool = False
    demo_data: bool = False


class CriticalResultsDashboardResponse(BaseModel):
    meta: CriticalResultsMeta
    summary: CriticalSummary
    urgent_banner: CriticalUrgentBanner
    alerts: List[CriticalAlertRow] = Field(default_factory=list)
    compliance_advisory: CriticalComplianceAdvisory

    model_config = ConfigDict(from_attributes=True)


class CriticalResultsActionResponse(BaseModel):
    message: str
    updated_alert_id: str
    status: NotifyStatus

