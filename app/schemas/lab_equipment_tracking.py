"""
Schemas for Equipment Tracking UI.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


EquipmentUiStatus = Literal["OPERATIONAL", "MAINTENANCE", "CALIBRATION_DUE", "INACTIVE"]


class EquipmentTrackingStatCards(BaseModel):
    total_equipment: int = 0
    operational: int = 0
    maintenance: int = 0
    calibration_due: int = 0


class EquipmentTrackingRow(BaseModel):
    equipment_id: UUID
    equipment_code: str
    name: str
    equipment_type: str
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_no: Optional[str] = None
    location: Optional[str] = None
    status: EquipmentUiStatus


class MaintenanceLogTrackingRow(BaseModel):
    equipment: str
    maintenance_type: str
    date: str
    performed_by: str
    cost: Optional[float] = None
    description: str = ""


class EquipmentTrackingMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class EquipmentTrackingDashboardResponse(BaseModel):
    meta: EquipmentTrackingMeta
    stats: EquipmentTrackingStatCards
    equipment_list: List[EquipmentTrackingRow] = Field(default_factory=list)
    maintenance_logs: List[MaintenanceLogTrackingRow] = Field(default_factory=list)
    quick_actions: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class AddEquipmentTrackingRequest(BaseModel):
    equipment_name: str = Field(..., min_length=2, max_length=160)
    equipment_type: str = Field(..., min_length=2, max_length=80)
    brand: str = Field(..., min_length=2, max_length=120)
    model: str = Field(..., min_length=1, max_length=120)
    serial_number: str = Field(..., min_length=1, max_length=120)
    location: str = Field(..., min_length=2, max_length=160)
    initial_status: Literal["OPERATIONAL", "MAINTENANCE", "INACTIVE"] = "OPERATIONAL"
    next_maintenance_date: Optional[date] = None


class AddEquipmentTrackingResponse(BaseModel):
    message: str
    equipment_id: UUID
    equipment_code: str
    status: EquipmentUiStatus


class EquipmentTrackingActionResponse(BaseModel):
    message: str
    action: str

