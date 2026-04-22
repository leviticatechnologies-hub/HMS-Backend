"""
Lab module (minimal) — equipment + maintenance only.

Legacy lab tables are dropped by migration `lab_v2_minimal_001` for existing DBs.
"""
import uuid
from sqlalchemy import (
    Column,
    DateTime,
    DECIMAL,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database_types import UUID_TYPE
from app.models.base import BaseModel

_JSON = JSONB


class Equipment(BaseModel):
    """Lab equipment (analyzers / instruments)."""

    __tablename__ = "lab_equipment"

    hospital_id = Column(UUID_TYPE, ForeignKey("hospitals.id"), nullable=False, index=True)

    equipment_code = Column(String(50), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(20), nullable=False)
    manufacturer = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    serial_number = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="ACTIVE")
    installation_date = Column(DateTime(timezone=True), nullable=True)
    last_calibrated_at = Column(DateTime(timezone=True), nullable=True)
    next_calibration_due_at = Column(DateTime(timezone=True), nullable=True)
    location = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    specifications = Column(_JSON, nullable=True, default=lambda: {})

    hospital = relationship("Hospital", back_populates="lab_equipment")
    maintenance_logs = relationship(
        "EquipmentMaintenanceLog", back_populates="equipment", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("hospital_id", "equipment_code", name="uq_equipment_code_per_hospital"),)

    def __repr__(self):
        return f"<Equipment(code='{self.equipment_code}', name='{self.name}')>"


class EquipmentMaintenanceLog(BaseModel):
    __tablename__ = "equipment_maintenance_logs"

    equipment_id = Column(UUID_TYPE, ForeignKey("lab_equipment.id"), nullable=False, index=True)
    # DB column is "type" (SQL keyword); Python name avoids shadowing.
    type_ = Column("type", String(20), nullable=False)
    performed_by = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    performed_at = Column(DateTime(timezone=True), nullable=False)
    next_due_at = Column(DateTime(timezone=True), nullable=True)
    remarks = Column(Text, nullable=True)
    attachment_ref = Column(String(500), nullable=True)
    cost = Column(DECIMAL(10, 2), nullable=True)
    service_provider = Column(String(200), nullable=True)
    service_ticket_no = Column(String(100), nullable=True)

    equipment = relationship("Equipment", back_populates="maintenance_logs")
    performed_by_user = relationship("User", foreign_keys=[performed_by])

    def __repr__(self):
        return f"<MaintenanceLog(equipment='{self.equipment_id}', type='{self.type_}')>"
