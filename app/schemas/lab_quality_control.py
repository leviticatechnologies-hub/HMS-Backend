"""
Schemas for Quality Control Workflows screen.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


QcRunStatus = Literal["PASSED", "WARNING", "FAILED"]
QcPriority = Literal["HIGH", "MEDIUM", "LOW"]


class QcStatCards(BaseModel):
    todays_qc_runs: int = 0
    passed_runs: int = 0
    warning_runs: int = 0
    failed_runs: int = 0


class QcRunRow(BaseModel):
    qc_id: str
    test: str
    qc_material: str
    lot_number: str
    date: str
    operator: str
    status: QcRunStatus
    observed_value: float


class QcMaterialRow(BaseModel):
    material_name: str
    material_type: str
    manufacturer: str
    lot_number: str
    expiry_date: str
    storage: str
    quantity: int


class QcRuleRow(BaseModel):
    rule_name: str
    description: str
    rule_type: str
    action_required: str
    priority: QcPriority


class QualityControlMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class QualityControlDashboardResponse(BaseModel):
    meta: QualityControlMeta
    stats: QcStatCards
    qc_runs: List[QcRunRow] = Field(default_factory=list)
    materials_inventory: List[QcMaterialRow] = Field(default_factory=list)
    rules: List[QcRuleRow] = Field(default_factory=list)
    workflow_actions: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class RecordQcRunRequest(BaseModel):
    test: str = Field(..., min_length=2, max_length=120)
    qc_material: str = Field(..., min_length=2, max_length=120)
    lot_number: str = Field(..., min_length=1, max_length=80)
    observed_value: float
    operator: str = Field(..., min_length=2, max_length=120)
    date: str = Field(..., min_length=8, max_length=20)


class RecordQcRunResponse(BaseModel):
    message: str
    qc_id: str
    status: QcRunStatus


class QcWorkflowActionResponse(BaseModel):
    message: str
    action: str

