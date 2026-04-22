"""Minimal lab service re-export (equipment + maintenance)."""

from app.services.lab_equipment_service import LabEquipmentService, LabService

__all__ = ["LabEquipmentService", "LabService"]
