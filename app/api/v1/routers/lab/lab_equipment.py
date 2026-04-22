"""
Lab equipment + maintenance API (minimal lab module).
"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.models.user import User
from app.core.security import require_roles
from app.services.lab_service import LabService
from app.schemas.lab_equipment import (
    EquipmentCreateRequest,
    EquipmentListResponse,
    EquipmentResponse,
    EquipmentStatusUpdateRequest,
    EquipmentUpdateRequest,
    MaintenanceLogCreateRequest,
    MaintenanceLogListResponse,
    MaintenanceLogResponse,
    MessageResponse,
)
from app.core.enums import EquipmentCategory, EquipmentStatus, MaintenanceType

router = APIRouter(
    prefix="/lab/equipment-qc",
    tags=["Lab - Equipment"],
    responses={404: {"description": "Not found"}},
)


@router.post("/equipment", response_model=EquipmentResponse)
async def create_equipment(
    equipment_data: EquipmentCreateRequest,
    current_user: User = Depends(
        require_roles(["LAB_ADMIN", "LAB_SUPERVISOR", "HOSPITAL_ADMIN", "LAB_TECH"])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        payload = equipment_data.model_dump()
        payload["name"] = payload.pop("equipment_name", None) or payload.get("name")
        out = await lab.create_equipment(payload)
        return EquipmentResponse.from_service_dict(await lab.get_equipment_by_id(out["equipment_id"]))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "EQUIPMENT_CREATION_FAILED", "message": str(e)},
        )


@router.get("/equipment", response_model=EquipmentListResponse)
async def get_equipment_list(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    category: Optional[EquipmentCategory] = Query(None),
    status: Optional[EquipmentStatus] = Query(None),
    active_only: bool = Query(True),
    current_user: User = Depends(
        require_roles(
            [
                "LAB_TECH",
                "LAB_SUPERVISOR",
                "LAB_ADMIN",
                "HOSPITAL_ADMIN",
                "PATHOLOGIST",
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        data = await lab.get_equipment_list(
            page=page,
            limit=limit,
            category_filter=category.value if category else None,
            status_filter=status.value if status else None,
            active_only=active_only,
        )
        return EquipmentListResponse(
            equipment=[EquipmentResponse.from_service_dict(x) for x in data["equipment"]],
            pagination=data["pagination"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "EQUIPMENT_FETCH_FAILED", "message": str(e)},
        )


@router.get("/equipment/{equipment_id}", response_model=EquipmentResponse)
async def get_equipment(
    equipment_id: uuid.UUID,
    current_user: User = Depends(
        require_roles(
            [
                "LAB_TECH",
                "LAB_SUPERVISOR",
                "LAB_ADMIN",
                "HOSPITAL_ADMIN",
                "PATHOLOGIST",
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        d = await lab.get_equipment_by_id(equipment_id)
        return EquipmentResponse.from_service_dict(d)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "EQUIPMENT_FETCH_FAILED", "message": str(e)},
        )


@router.put("/equipment/{equipment_id}", response_model=EquipmentResponse)
async def update_equipment(
    equipment_id: uuid.UUID,
    equipment_data: EquipmentUpdateRequest,
    current_user: User = Depends(
        require_roles(["LAB_ADMIN", "LAB_SUPERVISOR", "HOSPITAL_ADMIN", "LAB_TECH"])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        await lab.update_equipment(
            equipment_id=equipment_id,
            update_data=equipment_data.model_dump(exclude_unset=True),
        )
        d = await lab.get_equipment_by_id(equipment_id)
        return EquipmentResponse.from_service_dict(d)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "EQUIPMENT_UPDATE_FAILED", "message": str(e)},
        )


@router.patch("/equipment/{equipment_id}/status", response_model=MessageResponse)
async def update_equipment_status(
    equipment_id: uuid.UUID,
    status_data: EquipmentStatusUpdateRequest,
    current_user: User = Depends(
        require_roles(["LAB_ADMIN", "LAB_SUPERVISOR", "HOSPITAL_ADMIN", "LAB_TECH"])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        r = await lab.update_equipment_status(
            equipment_id=equipment_id,
            new_status=status_data.status,
            reason=status_data.reason,
        )
        return MessageResponse(message=r["message"], status="success")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "STATUS_UPDATE_FAILED", "message": str(e)},
        )


@router.get("/equipment/{equipment_id}/logs", response_model=MaintenanceLogListResponse)
async def get_equipment_logs(
    equipment_id: uuid.UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    maintenance_type: Optional[MaintenanceType] = Query(None),
    current_user: User = Depends(
        require_roles(
            [
                "LAB_TECH",
                "LAB_SUPERVISOR",
                "LAB_ADMIN",
                "HOSPITAL_ADMIN",
                "PATHOLOGIST",
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        data = await lab.get_maintenance_logs(
            equipment_id=equipment_id,
            page=page,
            limit=limit,
            maintenance_type=maintenance_type.value if maintenance_type else None,
        )
        return MaintenanceLogListResponse(
            logs=[MaintenanceLogResponse.from_service_dict(x) for x in data["logs"]],
            pagination=data["pagination"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "LOGS_FETCH_FAILED", "message": str(e)},
        )


@router.post("/equipment/{equipment_id}/logs", response_model=MaintenanceLogResponse)
async def create_maintenance_log(
    equipment_id: uuid.UUID,
    log_data: MaintenanceLogCreateRequest,
    current_user: User = Depends(
        require_roles(["LAB_ADMIN", "LAB_SUPERVISOR", "HOSPITAL_ADMIN", "LAB_TECH"])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        body = log_data.model_dump()
        body["type"] = body.get("log_type") or body.get("type")
        await lab.create_maintenance_log(
            equipment_id=equipment_id,
            log_data=body,
            performed_by=current_user.id,
        )
        logs = await lab.get_maintenance_logs(
            equipment_id=equipment_id,
            page=1,
            limit=1,
        )
        if not logs["logs"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "LOG_FETCH_FAILED", "message": "Failed to load created log"},
            )
        return MaintenanceLogResponse.from_service_dict(logs["logs"][0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "LOG_CREATION_FAILED", "message": str(e)},
        )


@router.get("/equipment/logs", response_model=MaintenanceLogListResponse)
async def get_all_maintenance_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    maintenance_type: Optional[MaintenanceType] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    current_user: User = Depends(
        require_roles(
            [
                "LAB_TECH",
                "LAB_SUPERVISOR",
                "LAB_ADMIN",
                "HOSPITAL_ADMIN",
                "PATHOLOGIST",
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        data = await lab.get_maintenance_logs(
            page=page,
            limit=limit,
            maintenance_type=maintenance_type.value if maintenance_type else None,
            date_from=date_from,
            date_to=date_to,
        )
        return MaintenanceLogListResponse(
            logs=[MaintenanceLogResponse.from_service_dict(x) for x in data["logs"]],
            pagination=data["pagination"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "LOGS_FETCH_FAILED", "message": str(e)},
        )


@router.get("/equipment/logs/{log_id}", response_model=MaintenanceLogResponse)
async def get_maintenance_log(
    log_id: uuid.UUID,
    current_user: User = Depends(
        require_roles(
            [
                "LAB_TECH",
                "LAB_SUPERVISOR",
                "LAB_ADMIN",
                "HOSPITAL_ADMIN",
                "PATHOLOGIST",
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        lab = LabService(db, current_user.hospital_id)
        d = await lab.get_maintenance_log_by_id(log_id)
        return MaintenanceLogResponse.from_service_dict(d)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "LOG_FETCH_FAILED", "message": str(e)},
        )
