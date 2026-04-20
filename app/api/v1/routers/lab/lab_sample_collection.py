"""
TASK 3: Lab Sample Collection & Barcode Management API endpoints.
Provides sample collection workflow with barcode generation and status tracking.
"""
import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import get_current_user
from app.models.user import User
from app.core.enums import (
    UserRole, SampleType, SampleStatus, ContainerType, RejectionReason
)
from app.services.lab_service import LabService
from app.core.utils import generate_barcode_png_bytes
from app.schemas.lab import (
    SampleCreateRequest, SampleCreateResponse, SampleResponse, SampleListResponse,
    SampleCollectRequest, SampleReceiveRequest, SampleRejectRequest,
    BulkCollectRequest, BulkCollectResponse, BarcodeResponse, MessageResponse
)

router = APIRouter(prefix="/lab/samples", tags=["Lab - Sample Collection"])


# ============================================================================
# DEPENDENCY FUNCTIONS
# ============================================================================

async def get_lab_service(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
) -> LabService:
    """Get Lab service instance with hospital isolation"""
    if not current_user.hospital_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NO_HOSPITAL_CONTEXT",
                "message": "Hospital context not found"
            }
        )
    
    return LabService(db, current_user.hospital_id)


async def verify_lab_tech_role(current_user: User = Depends(get_current_user)) -> User:
    """Verify current user has LAB_TECH role for lab operations"""
    user_roles = [role.name for role in current_user.roles]
    if UserRole.LAB_TECH not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INSUFFICIENT_PERMISSIONS",
                "message": "LAB_TECH role required for this operation"
            }
        )
    
    return current_user


# ============================================================================
# SAMPLE CREATION ENDPOINTS
# ============================================================================

@router.post("/orders/{order_id}/create", response_model=SampleCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_samples_for_order(
    order_id: str,
    sample_data: SampleCreateRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Create samples for a lab order.
    
    **Access Control:**
    - Only users with LAB_TECH role can create samples
    - Samples are automatically associated with user's hospital
    
    **Validation:**
    - Order must exist and belong to user's hospital
    - Order item IDs must be valid and belong to the order
    - Sample type must match test requirements
    - One sample can cover multiple tests of the same sample type
    
    **Business Rules:**
    - Sample number is auto-generated (SMP-YYYY-NNNNN format)
    - Barcode is auto-generated (LAB-ORD-{order_no}-SMP-{seq} format)
    - Sample status is set to REGISTERED by default
    - QR value defaults to same as barcode value
    
    **Sample Grouping:**
    - Tests requiring the same sample type can share one sample
    - Each sample type requires a separate sample container
    - Sample-to-test mapping is tracked in bridge table
    """
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_ORDER_ID",
                "message": "Invalid order ID format"
            }
        )
    
    result = await service.create_samples_for_order(
        order_uuid,
        sample_data.model_dump()["samples"],
        str(current_user.id),
    )
    return SampleCreateResponse(**result)


@router.get("/orders/{order_id}", response_model=List[SampleResponse])
async def get_samples_for_order(
    order_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get all samples for a specific lab order.
    
    **Access Control:**
    - Available to LAB_TECH role only
    - Hospital isolation enforced
    
    **Returns:**
    - List of all samples created for the order
    - Each sample includes associated tests
    - Collection and processing status for each sample
    - Barcode and QR values for scanning
    """
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_ORDER_ID",
                "message": "Invalid order ID format"
            }
        )
    
    samples = await service.get_samples_for_order(order_uuid)
    return [SampleResponse(**sample) for sample in samples]


# ============================================================================
# SAMPLE SEARCH & LISTING ENDPOINTS
# ============================================================================

@router.get("", response_model=SampleListResponse)
async def list_samples(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[SampleStatus] = Query(None, description="Filter by sample status"),
    sample_type: Optional[SampleType] = Query(None, description="Filter by sample type"),
    patient_id: Optional[str] = Query(None, description="Filter by patient ID (partial match)"),
    order_no: Optional[str] = Query(None, description="Filter by order number (partial match)"),
    date_from: Optional[str] = Query(None, description="Filter samples from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter samples to date (YYYY-MM-DD)"),
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Search and filter samples with pagination.
    
    **Access Control:**
    - Available to LAB_TECH role only
    - Results are automatically filtered to user's hospital
    
    **Filtering Options:**
    - **status**: Filter by sample status (REGISTERED, COLLECTED, IN_PROCESS, REJECTED)
    - **sample_type**: Filter by sample type (BLOOD, URINE, SWAB, etc.)
    - **patient_id**: Partial match search in patient ID
    - **order_no**: Partial match search in lab order number
    - **date_from**: Filter samples created from this date
    - **date_to**: Filter samples created to this date
    
    **Pagination:**
    - Default: 50 items per page
    - Maximum: 100 items per page
    - Samples are sorted by creation date (newest first)
    
    **Use Cases:**
    - Find samples by patient
    - Track sample collection progress
    - Monitor sample processing workflow
    - Search samples by barcode scanning
    """
    # Parse date filters
    date_from_dt = None
    date_to_dt = None
    
    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_DATE_FORMAT",
                    "message": "date_from must be in YYYY-MM-DD format"
                }
            )
    
    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_DATE_FORMAT",
                    "message": "date_to must be in YYYY-MM-DD format"
                }
            )
    
    result = await service.get_samples(
        page=page,
        limit=limit,
        status_filter=status,
        sample_type_filter=sample_type,
        patient_id_filter=patient_id,
        order_no_filter=order_no,
        date_from=date_from_dt,
        date_to=date_to_dt
    )
    return SampleListResponse(**result)


# NOTE: Register /scan, /utils, /stats, /bulk BEFORE /{sample_id} so those segments are not
# captured as a sample UUID.


@router.get("/scan/{barcode_value}", response_model=SampleResponse)
async def scan_sample_barcode(
    barcode_value: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """Find sample by scanning barcode or QR code."""
    result = await service.scan_sample_by_barcode(barcode_value)
    return SampleResponse(**result)


@router.get("/utils/container-types", response_model=dict)
async def get_container_types(
    current_user: User = Depends(verify_lab_tech_role)
):
    return {
        "container_types": [
            {"value": container.value, "label": container.value.replace("_", " ").title()}
            for container in ContainerType
        ]
    }


@router.get("/utils/sample-statuses", response_model=dict)
async def get_sample_statuses(
    current_user: User = Depends(verify_lab_tech_role)
):
    return {
        "statuses": [
            {"value": s.value, "label": s.value.replace("_", " ").title()}
            for s in SampleStatus
        ]
    }


@router.get("/utils/rejection-reasons", response_model=dict)
async def get_rejection_reasons(
    current_user: User = Depends(verify_lab_tech_role)
):
    return {
        "rejection_reasons": [
            {"value": reason.value, "label": reason.value.replace("_", " ").title()}
            for reason in RejectionReason
        ]
    }


@router.get("/stats", response_model=dict)
async def get_sample_collection_statistics(
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    stats = await service.get_sample_collection_statistics()
    return stats


@router.post("/bulk/collect", response_model=BulkCollectResponse)
async def bulk_collect_samples(
    bulk_data: BulkCollectRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    result = await service.bulk_collect_samples(
        bulk_data.model_dump()["samples"],
        str(current_user.id)
    )
    return BulkCollectResponse(**result)


@router.get("/{sample_id}", response_model=SampleResponse)
async def get_sample_details(
    sample_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get detailed information about a specific sample.
    
    **Access Control:**
    - Available to LAB_TECH role only
    - Hospital isolation enforced
    
    **Returns:**
    - Complete sample information including all fields
    - List of all tests associated with the sample
    - Collection and processing timeline
    - Barcode and QR values
    - Current status and location
    """
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_SAMPLE_ID",
                "message": "Invalid sample ID format"
            }
        )
    
    result = await service.get_sample_by_id(sample_uuid)
    return SampleResponse(**result)


# ============================================================================
# BARCODE & QR CODE ENDPOINTS
# ============================================================================

@router.get("/{sample_id}/barcode", response_model=BarcodeResponse)
async def get_sample_barcode(
    sample_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get barcode information for a sample.
    
    **Access Control:**
    - Available to LAB_TECH role only
    - Hospital isolation enforced
    
    **Returns:**
    - Barcode value and QR value
    - Barcode format information (CODE128)
    - Display text for barcode labels
    - Sample metadata for barcode rendering
    
    **Use Cases:**
    - Generate barcode labels for printing
    - Display barcode in mobile apps
    - Barcode scanning validation
    - Sample identification workflows
    """
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_SAMPLE_ID",
                "message": "Invalid sample ID format"
            }
        )
    
    result = await service.get_sample_barcode(sample_uuid)
    return BarcodeResponse(**result)


@router.get("/{sample_id}/barcode.png")
async def get_sample_barcode_image(
    sample_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get barcode as PNG image (generated on-the-fly, no file saved).
    
    **Access Control:** LAB_TECH role, hospital isolation enforced.
    
    **Returns:** PNG image suitable for printing labels or display in browser.
    """
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SAMPLE_ID", "message": "Invalid sample ID format"}
        )
    
    barcode_data = await service.get_sample_barcode(sample_uuid)
    barcode_value = barcode_data.get("barcode") or barcode_data.get("barcode_value") or barcode_data.get("sample_no", "")
    
    png_bytes = generate_barcode_png_bytes(barcode_value)
    if not png_bytes:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "BARCODE_IMAGE_UNAVAILABLE",
                "message": "Barcode image generation requires python-barcode. Install with: pip install \"python-barcode[images]\""
            }
        )
    
    return Response(content=png_bytes, media_type="image/png")


# ============================================================================
# SAMPLE STATUS UPDATE ENDPOINTS
# ============================================================================

@router.patch("/{sample_id}/collect", response_model=MessageResponse)
async def collect_sample(
    sample_id: str,
    collect_data: SampleCollectRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Mark sample as collected (REGISTERED → COLLECTED).
    
    **Access Control:**
    - Only users with LAB_TECH role can collect samples
    - Hospital isolation enforced
    
    **Status Transition Rules:**
    - Sample must be in REGISTERED status
    - Cannot collect already collected samples
    - Cannot collect rejected samples
    - Status changes to COLLECTED
    
    **Collection Details Recorded:**
    - Who collected (current user)
    - When collected (current timestamp)
    - Where collected (collection site)
    - Collection notes
    - Actual volume collected
    
    **Business Rules:**
    - Collection timestamp is automatically set
    - Collector is automatically set to current user
    - Collection cannot be undone (no backward transitions)
    - Collection site is optional but recommended
    """
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_SAMPLE_ID",
                "message": "Invalid sample ID format"
            }
        )
    
    result = await service.collect_sample(
        sample_uuid,
        collect_data.model_dump(),
        str(current_user.id),
    )
    return MessageResponse(**result)


@router.patch("/{sample_id}/receive", response_model=MessageResponse)
async def receive_sample_in_lab(
    sample_id: str,
    receive_data: SampleReceiveRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Mark sample as received in lab (COLLECTED → RECEIVED).
    Use PATCH /{sample_id}/start-analysis to move to IN_PROCESS before result entry.
    """
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SAMPLE_ID", "message": "Invalid sample ID format"},
        )
    result = await service.receive_sample(
        sample_uuid, receive_data.model_dump(), str(current_user.id)
    )
    return MessageResponse(**result)


@router.patch("/{sample_id}/start-analysis", response_model=MessageResponse)
async def start_analysis_sample(
    sample_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """Transition sample RECEIVED → IN_PROCESS (lab starts analysis). Required before result entry."""
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SAMPLE_ID", "message": "Invalid sample ID format"},
        )
    result = await service.start_analysis_sample(sample_uuid, str(current_user.id))
    return MessageResponse(message=result["message"], data=result)


@router.patch("/{sample_id}/store", response_model=MessageResponse)
async def store_sample(
    sample_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """Transition sample IN_PROCESS → STORED (sample stored after use)."""
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SAMPLE_ID", "message": "Invalid sample ID format"},
        )
    result = await service.store_sample(sample_uuid)
    return MessageResponse(message=result["message"], data=result)


@router.patch("/{sample_id}/discard", response_model=MessageResponse)
async def discard_sample(
    sample_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """Transition sample IN_PROCESS → DISCARDED."""
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SAMPLE_ID", "message": "Invalid sample ID format"},
        )
    result = await service.discard_sample(sample_uuid)
    return MessageResponse(message=result["message"], data=result)


@router.patch("/{sample_id}/reject", response_model=MessageResponse)
async def reject_sample(
    sample_id: str,
    reject_data: SampleRejectRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Reject a sample due to quality issues.
    
    **Access Control:**
    - Only users with LAB_TECH role can reject samples
    - Hospital isolation enforced
    
    **Rejection Rules:**
    - Can reject samples in any status except already rejected
    - Rejection is permanent (cannot be undone)
    - Rejection reason is mandatory
    - Detailed rejection notes required
    
    **Rejection Reasons:**
    - **HEMOLYZED**: Blood sample shows hemolysis
    - **INSUFFICIENT_VOLUME**: Not enough sample volume
    - **WRONG_LABEL**: Incorrect or missing labels
    - **LEAKED**: Container leaked during transport
    - **CONTAMINATED**: Sample contamination detected
    - **EXPIRED_CONTAINER**: Collection container expired
    - **CLOTTED**: Blood sample clotted inappropriately
    - **OTHER**: Other quality issues
    
    **Business Rules:**
    - Rejection timestamp is automatically set
    - Rejector is automatically set to current user
    - Rejected samples cannot be processed for testing
    - New sample collection may be required
    """
    try:
        sample_uuid = uuid.UUID(sample_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_SAMPLE_ID",
                "message": "Invalid sample ID format"
            }
        )
    
    result = await service.reject_sample(
        sample_uuid, reject_data.model_dump(), str(current_user.id)
    )
    return MessageResponse(**result)