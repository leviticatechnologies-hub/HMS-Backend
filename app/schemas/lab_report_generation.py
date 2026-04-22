"""
Schemas for Lab Report Generation screen.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


ReportTemplate = Literal["STANDARD", "COMPREHENSIVE", "DOCTOR_SUMMARY", "PATIENT_FRIENDLY", "CUSTOM"]
ReportStatus = Literal["READY", "PENDING_REVIEW", "DRAFT"]


class ReportGenerationRow(BaseModel):
    report_id: str
    patient_name: str
    test_type: str
    completion_date: date
    status: ReportStatus
    verified_by: Optional[str] = None


class ReportGenerationSummary(BaseModel):
    total_reports: int = 0
    ready_reports: int = 0
    pending_review: int = 0
    test_types: int = 0


class ReportGenerationMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class ReportGenerationListResponse(BaseModel):
    meta: ReportGenerationMeta
    selected_template: ReportTemplate
    templates: List[ReportTemplate] = Field(default_factory=list)
    summary: ReportGenerationSummary
    rows: List[ReportGenerationRow] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ReadyTestForReportRow(BaseModel):
    patient_name: str
    patient_ref: str = Field(
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    test_type: str
    completed_on: date
    source_test_id: str

    model_config = ConfigDict(populate_by_name=True)


class ReadyTestsResponse(BaseModel):
    rows: List[ReadyTestForReportRow] = Field(default_factory=list)


class GenerateReportRequest(BaseModel):
    source_test_id: str
    template: ReportTemplate = "STANDARD"


class GenerateReportResponse(BaseModel):
    message: str
    report_id: str
    status: ReportStatus


class ReportPreviewResponse(BaseModel):
    report_id: str
    title: str
    patient_name: str
    test_type: str
    status: ReportStatus
    template: ReportTemplate
    preview_text: str


class PrintReportResponse(BaseModel):
    message: str
    report_id: str
    print_job_id: str

