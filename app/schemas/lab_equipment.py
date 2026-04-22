"""
Pydantic models for the minimal lab equipment API.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class EquipmentCreateRequest(BaseModel):
    equipment_code: str = Field(..., min_length=1, max_length=50)
    equipment_name: str = Field(..., min_length=2, max_length=255)
    category: str = Field(..., min_length=1, max_length=20)
    manufacturer: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    serial_number: Optional[str] = Field(None, max_length=100)
    location: Optional[str] = Field(None, max_length=100)
    installation_date: Optional[datetime] = None
    next_calibration_due_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=1000)
    specifications: Optional[Dict[str, Any]] = None

    @field_validator("equipment_code", mode="before")
    @classmethod
    def upper_code(cls, v: str) -> str:
        if isinstance(v, str):
            return v.upper().strip()
        return v


class EquipmentUpdateRequest(BaseModel):
    """Partial update; `equipment_name` maps to model column `name`."""

    equipment_name: Optional[str] = Field(None, min_length=2, max_length=255)
    category: Optional[str] = None
    manufacturer: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    serial_number: Optional[str] = Field(None, max_length=100)
    location: Optional[str] = Field(None, max_length=100)
    installation_date: Optional[datetime] = None
    last_calibrated_at: Optional[datetime] = None
    next_calibration_due_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=1000)
    specifications: Optional[Dict[str, Any]] = None


class EquipmentStatusUpdateRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=20)
    reason: Optional[str] = Field(None, max_length=500)


class EquipmentResponse(BaseModel):
    equipment_id: UUID
    equipment_code: str
    equipment_name: str
    category: str
    manufacturer: Optional[str]
    model: Optional[str]
    serial_number: Optional[str]
    status: str
    location: Optional[str]
    installation_date: Optional[datetime]
    last_calibrated_at: Optional[datetime]
    next_calibration_due_at: Optional[datetime]
    notes: Optional[str]
    specifications: Optional[Dict[str, Any]]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_service_dict(cls, d: Dict[str, Any]) -> "EquipmentResponse":
        return cls(
            equipment_id=d["equipment_id"],
            equipment_code=d["equipment_code"],
            equipment_name=d.get("equipment_name") or d.get("name", ""),
            category=d["category"],
            manufacturer=d.get("manufacturer"),
            model=d.get("model"),
            serial_number=d.get("serial_number"),
            status=d["status"],
            location=d.get("location"),
            installation_date=d.get("installation_date"),
            last_calibrated_at=d.get("last_calibrated_at"),
            next_calibration_due_at=d.get("next_calibration_due_at"),
            notes=d.get("notes"),
            specifications=d.get("specifications"),
            is_active=d.get("is_active", True),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )


class EquipmentListResponse(BaseModel):
    equipment: List[EquipmentResponse]
    pagination: Dict[str, Any]


class MaintenanceLogCreateRequest(BaseModel):
    """`type` in JSON is accepted as log_type in Python (reserved name)."""

    log_type: str = Field(
        ...,
        min_length=1,
        max_length=20,
        validation_alias=AliasChoices("type", "log_type"),
        description="e.g. CALIBRATION, PREVENTIVE, BREAKDOWN",
    )
    performed_at: datetime
    next_due_at: Optional[datetime] = None
    remarks: Optional[str] = None
    attachment_ref: Optional[str] = None
    cost: Optional[Decimal] = None
    service_provider: Optional[str] = None
    service_ticket_no: Optional[str] = None


class MaintenanceLogResponse(BaseModel):
    log_id: UUID
    equipment_id: UUID
    equipment_code: str
    equipment_name: str
    log_type: str = Field(validation_alias=AliasChoices("type", "log_type"), serialization_alias="type")
    performed_by: UUID
    performed_at: datetime
    next_due_at: Optional[datetime]
    remarks: Optional[str]
    attachment_ref: Optional[str]
    cost: Optional[Decimal]
    service_provider: Optional[str]
    service_ticket_no: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_service_dict(cls, d: Dict[str, Any]) -> "MaintenanceLogResponse":
        copy = {**d}
        if "type" in copy and "log_type" not in copy:
            copy["log_type"] = copy["type"]
        return cls.model_validate(copy)


class MaintenanceLogListResponse(BaseModel):
    logs: List[MaintenanceLogResponse]
    pagination: Dict[str, Any]


class MessageResponse(BaseModel):
    message: str
    status: str = "success"
