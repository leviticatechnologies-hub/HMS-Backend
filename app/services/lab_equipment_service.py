"""
Minimal lab service: equipment + maintenance logs only.
"""
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, asc, desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import ensure_datetime_utc_aware
from app.models.lab import Equipment, EquipmentMaintenanceLog

__all__ = ["LabEquipmentService", "LabService"]


class LabEquipmentService:
    def __init__(self, db: AsyncSession, hospital_id: uuid.UUID):
        self.db = db
        self.hospital_id = hospital_id

    async def create_equipment(self, equipment_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            existing_equipment = await self.db.execute(
                select(Equipment).where(
                    and_(
                        Equipment.hospital_id == self.hospital_id,
                        Equipment.equipment_code == equipment_data["equipment_code"].upper(),
                    )
                )
            )

            if existing_equipment.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "DUPLICATE_EQUIPMENT_CODE",
                        "message": f"Equipment code '{equipment_data['equipment_code']}' already exists in this hospital",
                    },
                )

            name = equipment_data.get("name") or equipment_data.get("equipment_name")
            if not name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "VALIDATION_ERROR", "message": "Equipment name is required"},
                )
            equipment = Equipment(
                hospital_id=self.hospital_id,
                equipment_code=equipment_data["equipment_code"].upper(),
                name=name,
                category=equipment_data["category"],
                manufacturer=equipment_data.get("manufacturer"),
                model=equipment_data.get("model"),
                serial_number=equipment_data.get("serial_number"),
                location=equipment_data.get("location"),
                installation_date=equipment_data.get("installation_date"),
                next_calibration_due_at=equipment_data.get("next_calibration_due_at"),
                notes=equipment_data.get("notes"),
                specifications=equipment_data.get("specifications"),
                status="ACTIVE",
                is_active=True,
            )

            self.db.add(equipment)
            await self.db.commit()
            await self.db.refresh(equipment)

            return {
                "equipment_id": equipment.id,
                "equipment_code": equipment.equipment_code,
                "equipment_name": equipment.name,
                "category": equipment.category,
                "status": equipment.status,
                "message": "Equipment created successfully",
            }

        except HTTPException:
            raise
        except IntegrityError:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "DATABASE_ERROR",
                    "message": "Failed to create equipment due to database constraint",
                },
            )
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "CREATION_FAILED", "message": f"Failed to create equipment: {str(e)}"},
            )

    async def get_equipment_list(
        self,
        page: int = 1,
        limit: int = 50,
        category_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        active_only: bool = True,
    ) -> Dict[str, Any]:
        try:
            conditions = [Equipment.hospital_id == self.hospital_id]

            if active_only:
                conditions.append(Equipment.is_active == True)  # noqa: E712

            if category_filter:
                conditions.append(Equipment.category == category_filter)

            if status_filter:
                conditions.append(Equipment.status == status_filter)

            count_query = select(func.count(Equipment.id)).where(and_(*conditions))
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit if total else 0

            equipment_query = (
                select(Equipment)
                .where(and_(*conditions))
                .order_by(asc(Equipment.equipment_code))
                .offset(offset)
                .limit(limit)
            )

            equipment_result = await self.db.execute(equipment_query)
            equipment_list = equipment_result.scalars().all()

            equipment_responses = []
            for equipment in equipment_list:
                equipment_responses.append(
                    {
                        "equipment_id": equipment.id,
                        "equipment_code": equipment.equipment_code,
                        "equipment_name": equipment.name,
                        "category": equipment.category,
                        "manufacturer": equipment.manufacturer,
                        "model": equipment.model,
                        "serial_number": equipment.serial_number,
                        "status": equipment.status,
                        "location": equipment.location,
                        "installation_date": equipment.installation_date,
                        "last_calibrated_at": equipment.last_calibrated_at,
                        "next_calibration_due_at": equipment.next_calibration_due_at,
                        "notes": equipment.notes,
                        "specifications": equipment.specifications,
                        "is_active": equipment.is_active,
                        "created_at": equipment.created_at,
                        "updated_at": equipment.updated_at,
                    }
                )

            return {
                "equipment": equipment_responses,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1,
                },
            }

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "FETCH_FAILED", "message": f"Failed to fetch equipment: {str(e)}"},
            )

    async def get_equipment_by_id(self, equipment_id: uuid.UUID) -> Dict[str, Any]:
        try:
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(Equipment.id == equipment_id, Equipment.hospital_id == self.hospital_id)
                )
            )

            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "EQUIPMENT_NOT_FOUND", "message": f"Equipment with ID {equipment_id} not found"},
                )

            return {
                "equipment_id": equipment.id,
                "equipment_code": equipment.equipment_code,
                "equipment_name": equipment.name,
                "category": equipment.category,
                "manufacturer": equipment.manufacturer,
                "model": equipment.model,
                "serial_number": equipment.serial_number,
                "status": equipment.status,
                "location": equipment.location,
                "installation_date": equipment.installation_date,
                "last_calibrated_at": equipment.last_calibrated_at,
                "next_calibration_due_at": equipment.next_calibration_due_at,
                "notes": equipment.notes,
                "specifications": equipment.specifications,
                "is_active": equipment.is_active,
                "created_at": equipment.created_at,
                "updated_at": equipment.updated_at,
            }

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "FETCH_FAILED", "message": f"Failed to fetch equipment: {str(e)}"},
            )

    async def update_equipment(self, equipment_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(Equipment.id == equipment_id, Equipment.hospital_id == self.hospital_id)
                )
            )

            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "EQUIPMENT_NOT_FOUND", "message": f"Equipment with ID {equipment_id} not found"},
                )

            update_fields = {k: v for k, v in update_data.items() if v is not None}
            if "equipment_name" in update_fields:
                update_fields["name"] = update_fields.pop("equipment_name")

            if not update_fields:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "NO_UPDATE_DATA", "message": "No valid update data provided"},
                )

            await self.db.execute(update(Equipment).where(Equipment.id == equipment_id).values(**update_fields))
            await self.db.commit()

            return {
                "equipment_id": equipment_id,
                "equipment_code": equipment.equipment_code,
                "equipment_name": equipment.name,
                "message": "Equipment updated successfully",
            }

        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "UPDATE_FAILED", "message": f"Failed to update equipment: {str(e)}"},
            )

    async def update_equipment_status(
        self, equipment_id: uuid.UUID, new_status: str, reason: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(Equipment.id == equipment_id, Equipment.hospital_id == self.hospital_id)
                )
            )

            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "EQUIPMENT_NOT_FOUND", "message": f"Equipment with ID {equipment_id} not found"},
                )

            await self.db.execute(update(Equipment).where(Equipment.id == equipment_id).values(status=new_status))
            await self.db.commit()

            return {
                "message": f"Equipment status updated to {new_status}",
                "equipment_id": str(equipment_id),
                "equipment_code": equipment.equipment_code,
                "status": new_status,
                "reason": reason,
            }

        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "STATUS_UPDATE_FAILED", "message": f"Failed to update equipment status: {str(e)}"},
            )

    async def create_maintenance_log(
        self, equipment_id: uuid.UUID, log_data: Dict[str, Any], performed_by: UUID
    ) -> Dict[str, Any]:
        try:
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(Equipment.id == equipment_id, Equipment.hospital_id == self.hospital_id)
                )
            )

            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "EQUIPMENT_NOT_FOUND", "message": f"Equipment with ID {equipment_id} not found"},
                )

            maintenance_log = EquipmentMaintenanceLog(
                equipment_id=equipment_id,
                type_=log_data["type"],
                performed_by=performed_by,
                performed_at=log_data["performed_at"],
                next_due_at=log_data.get("next_due_at"),
                remarks=log_data.get("remarks"),
                attachment_ref=log_data.get("attachment_ref"),
                cost=log_data.get("cost"),
                service_provider=log_data.get("service_provider"),
                service_ticket_no=log_data.get("service_ticket_no"),
            )

            self.db.add(maintenance_log)

            if log_data["type"] == "CALIBRATION":
                await self.db.execute(
                    update(Equipment)
                    .where(Equipment.id == equipment_id)
                    .values(
                        last_calibrated_at=log_data["performed_at"],
                        next_calibration_due_at=log_data.get("next_due_at"),
                    )
                )

            await self.db.commit()
            await self.db.refresh(maintenance_log)

            return {
                "log_id": maintenance_log.id,
                "equipment_id": equipment_id,
                "equipment_code": equipment.equipment_code,
                "type": maintenance_log.type_,
                "performed_at": maintenance_log.performed_at,
                "message": "Maintenance log created successfully",
            }

        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "LOG_CREATION_FAILED", "message": f"Failed to create maintenance log: {str(e)}"},
            )

    async def get_maintenance_log_by_id(self, log_id: uuid.UUID) -> Dict[str, Any]:
        q = (
            select(EquipmentMaintenanceLog, Equipment)
            .join(Equipment, EquipmentMaintenanceLog.equipment_id == Equipment.id)
            .where(
                and_(
                    EquipmentMaintenanceLog.id == log_id,
                    Equipment.hospital_id == self.hospital_id,
                )
            )
        )
        res = await self.db.execute(q)
        row = res.first()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "LOG_NOT_FOUND", "message": f"Maintenance log {log_id} not found"},
            )
        log, equipment = row
        return {
            "log_id": log.id,
            "equipment_id": log.equipment_id,
            "equipment_code": equipment.equipment_code,
            "equipment_name": equipment.name,
            "type": log.type_,
            "performed_by": log.performed_by,
            "performed_at": log.performed_at,
            "next_due_at": log.next_due_at,
            "remarks": log.remarks,
            "attachment_ref": log.attachment_ref,
            "cost": log.cost,
            "service_provider": log.service_provider,
            "service_ticket_no": log.service_ticket_no,
            "created_at": log.created_at,
            "updated_at": log.updated_at,
        }

    async def get_maintenance_logs(
        self,
        equipment_id: Optional[uuid.UUID] = None,
        page: int = 1,
        limit: int = 50,
        maintenance_type: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        try:
            if date_from is not None:
                date_from = ensure_datetime_utc_aware(date_from)
            if date_to is not None:
                date_to = ensure_datetime_utc_aware(date_to)

            conditions = []

            if equipment_id:
                conditions.append(EquipmentMaintenanceLog.equipment_id == equipment_id)
            else:
                conditions.append(Equipment.hospital_id == self.hospital_id)

            if maintenance_type:
                conditions.append(EquipmentMaintenanceLog.type_ == maintenance_type)

            if date_from:
                conditions.append(EquipmentMaintenanceLog.performed_at >= date_from)

            if date_to:
                conditions.append(EquipmentMaintenanceLog.performed_at <= date_to)

            base_query = (
                select(EquipmentMaintenanceLog, Equipment)
                .join(Equipment, EquipmentMaintenanceLog.equipment_id == Equipment.id)
                .where(and_(*conditions))
            )

            count_query = select(func.count()).select_from(base_query.subquery())
            total_result = await self.db.execute(count_query)
            total = total_result.scalar() or 0
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit if total else 0

            logs_query = (
                base_query.order_by(desc(EquipmentMaintenanceLog.performed_at)).offset(offset).limit(limit)
            )

            logs_result = await self.db.execute(logs_query)
            logs_data = logs_result.all()

            logs_list = []
            for log, equipment in logs_data:
                logs_list.append(
                    {
                        "log_id": log.id,
                        "equipment_id": log.equipment_id,
                        "equipment_code": equipment.equipment_code,
                        "equipment_name": equipment.name,
                        "type": log.type_,
                        "performed_by": log.performed_by,
                        "performed_at": log.performed_at,
                        "next_due_at": log.next_due_at,
                        "remarks": log.remarks,
                        "attachment_ref": log.attachment_ref,
                        "cost": log.cost,
                        "service_provider": log.service_provider,
                        "service_ticket_no": log.service_ticket_no,
                        "created_at": log.created_at,
                        "updated_at": log.updated_at,
                    }
                )

            return {
                "logs": logs_list,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1,
                },
            }

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "FETCH_FAILED", "message": f"Failed to fetch maintenance logs: {str(e)}"},
            )


LabService = LabEquipmentService
