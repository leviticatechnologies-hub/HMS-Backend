"""
TASK 2: Lab Test Registration API endpoints.
Provides CRUD operations for lab tests and orders with role-based access control.
"""
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import get_current_user
from app.models.user import User
from app.core.enums import UserRole, SampleType, LabOrderSource, LabOrderPriority, LabOrderStatus
from app.services.lab_service import LabService
from app.schemas.lab import (
    CategoryCreateRequest, CategoryUpdateRequest, CategoryResponse, CategoryListResponse,
    CategoryCreateResponse, CategoryUpdateResponse,
    TestCreateRequest, TestUpdateRequest, TestResponse, TestListResponse,
    TestCreateResponse, TestUpdateResponse,
    LabOrderCreateRequest, LabOrderResponse, LabOrderCreateResponse, LabOrderListResponse,
    RegisterOrderResponse,
    PriorityUpdateRequest, CancelOrderRequest,
    OrderPriorityUpdateResponse, OrderCancelResponse,
)

router = APIRouter(prefix="/lab/registration", tags=["Lab - Test Registration"])


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


async def verify_lab_registration_role(current_user: User = Depends(get_current_user)) -> User:
    """Verify current user has LAB_TECH or RECEPTIONIST role for order registration/list/cancel"""
    user_roles = [role.name for role in current_user.roles]
    if UserRole.LAB_TECH not in user_roles and UserRole.RECEPTIONIST not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INSUFFICIENT_PERMISSIONS",
                "message": "LAB_TECH or RECEPTIONIST role required for this operation"
            }
        )
    return current_user


# ============================================================================
# LAB TEST CATEGORY ENDPOINTS
# ============================================================================

@router.post("/categories", response_model=CategoryCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_lab_category(
    category_data: CategoryCreateRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Create a new lab test category (department).
    Category code must be unique within the hospital.
    """
    result = await service.create_category(category_data.model_dump())
    return CategoryCreateResponse(**result)


@router.get("/categories", response_model=CategoryListResponse)
async def list_lab_categories(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    active: bool = Query(True, description="Filter active categories only"),
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """List lab test categories with pagination."""
    result = await service.list_categories(page=page, limit=limit, active_only=active)
    return CategoryListResponse(**result)


@router.get("/categories/{category_id}", response_model=CategoryResponse)
async def get_lab_category(
    category_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """Get a lab test category by ID."""
    try:
        cat_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_CATEGORY_ID", "message": "Invalid category ID format"})
    result = await service.get_category_by_id(cat_uuid)
    return CategoryResponse(
        category_id=result["category_id"],
        category_code=result["category_code"],
        name=result["name"],
        description=result.get("description"),
        display_order=result["display_order"],
        is_active=result["is_active"],
        created_at=result["created_at"],
        updated_at=result["updated_at"],
    )


@router.put("/categories/{category_id}", response_model=CategoryUpdateResponse)
async def update_lab_category(
    category_id: str,
    update_data: CategoryUpdateRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """Update a lab test category."""
    try:
        cat_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_CATEGORY_ID", "message": "Invalid category ID format"})
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    if not update_dict:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "NO_UPDATE_DATA", "message": "No valid update data provided"})
    result = await service.update_category(cat_uuid, update_dict)
    return CategoryUpdateResponse(**result)


# ============================================================================
# LAB TEST CATALOGUE ENDPOINTS
# ============================================================================

@router.post("/tests", response_model=TestCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_lab_test(
    test_data: TestCreateRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Create a new lab test in the catalogue.
    Test code must be unique within the hospital.
    Optional: category_id, unit, methodology, reference_ranges.
    """
    result = await service.create_test(test_data.model_dump())
    return TestCreateResponse(**result)


@router.get("/tests", response_model=TestListResponse)
async def list_lab_tests(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    active: bool = Query(True, description="Filter active tests only"),
    sample_type: Optional[SampleType] = Query(None, description="Filter by sample type"),
    category_id: Optional[str] = Query(None, description="Filter by category UUID"),
    search: Optional[str] = Query(None, min_length=1, description="Search by test code or name"),
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get paginated list of lab tests with filtering and search.
    Filters: active, sample_type, category_id. Search: test code or name.
    """
    cat_uuid = None
    if category_id:
        try:
            cat_uuid = uuid.UUID(category_id)
        except ValueError:
            cat_uuid = None
    result = await service.get_tests(
        page=page,
        limit=limit,
        active_only=active,
        sample_type_filter=sample_type,
        category_id=cat_uuid,
        search=search,
    )
    return TestListResponse(**result)


@router.get("/tests/{test_id}", response_model=TestResponse)
async def get_lab_test(
    test_id: str,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get detailed information about a specific lab test.
    
    **Access Control:**
    - Available to LAB_TECH role only
    - Hospital isolation enforced (can only access tests from own hospital)
    
    **Returns:**
    - Complete test information including all fields
    - Creation and update timestamps
    - Current status and settings
    """
    try:
        test_uuid = uuid.UUID(test_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_TEST_ID",
                "message": "Invalid test ID format"
            }
        )
    
    result = await service.get_test_by_id(test_uuid)
    return TestResponse(**result)


@router.put("/tests/{test_id}", response_model=TestUpdateResponse)
async def update_lab_test(
    test_id: str,
    update_data: TestUpdateRequest,
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Update lab test information (full update).
    
    **Access Control:**
    - Only users with LAB_TECH role can update tests
    - Hospital isolation enforced
    
    **Validation:**
    - Only provided fields are updated (partial update supported)
    - Cannot update non-existent tests
    
    **Business Rules:**
    - Updated timestamp is automatically set
    - Test code cannot be changed after creation
    """
    try:
        test_uuid = uuid.UUID(test_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_TEST_ID",
                "message": "Invalid test ID format"
            }
        )
    
    # Convert to dict, excluding None values
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    
    if not update_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NO_UPDATE_DATA",
                "message": "No valid update data provided"
            }
        )
    
    result = await service.update_test(test_uuid, update_dict)
    return TestUpdateResponse(**result)


# ============================================================================
# LAB ORDER ENDPOINTS
# ============================================================================

@router.post("/orders", response_model=LabOrderCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_lab_order(
    order_data: LabOrderCreateRequest,
    current_user: User = Depends(verify_lab_registration_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Create a new lab order with one or more tests.
    
    **Access Control:**
    - Only users with LAB_TECH role can create orders
    - Order is automatically associated with user's hospital
    
    **Validation:**
    - Patient ID must be provided
    - At least one test must be specified
    - All test IDs must exist and be active
    - Doctor ID required when source is DOCTOR
    
    **Business Rules:**
    - Lab order number is auto-generated (LAB-YYYY-NNNNN format)
    - Order status is set to REGISTERED by default
    - Hospital isolation is enforced automatically
    - Estimated completion time calculated from test turnaround times
    
    **Order Sources:**
    - **DOCTOR**: Order requested by a doctor (requires doctor ID)
    - **WALKIN**: Walk-in patient order (doctor ID optional)

    **create_as_draft**: If true, order is created as DRAFT; use POST /orders/{order_id}/register to submit.
    """
    result = await service.create_order(order_data.model_dump())
    return LabOrderCreateResponse(
        order_id=result["lab_order_id"],
        order_ref=result["lab_order_no"],
        message=result["message"],
        total_tests=result["total_tests"],
        status=result.get("status"),
    )


@router.post("/orders/{order_id}/register", response_model=RegisterOrderResponse)
async def register_lab_order(
    order_id: str,
    current_user: User = Depends(verify_lab_registration_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Register a DRAFT order (transition DRAFT -> REGISTERED).
    Only DRAFT orders can be registered. LAB_TECH or RECEPTIONIST.
    """
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_ORDER_ID", "message": "Invalid order ID format"})
    result = await service.register_order(order_uuid)
    return RegisterOrderResponse(**result)


@router.get("/orders", response_model=LabOrderListResponse)
async def list_lab_orders(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[LabOrderStatus] = Query(None, description="Filter by order status"),
    priority: Optional[LabOrderPriority] = Query(None, description="Filter by priority"),
    date_from: Optional[str] = Query(None, description="Filter orders from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter orders to date (YYYY-MM-DD)"),
    current_user: User = Depends(verify_lab_registration_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get paginated list of lab orders with filtering options.
    
    **Access Control:**
    - Available to LAB_TECH role only
    - Results are automatically filtered to user's hospital
    
    **Filtering Options:**
    - **status**: Filter by order status (REGISTERED, SAMPLE_COLLECTED, etc.)
    - **priority**: Filter by priority (ROUTINE, URGENT, STAT)
    - **date_from**: Filter orders from this date (YYYY-MM-DD format)
    - **date_to**: Filter orders to this date (YYYY-MM-DD format)
    
    **Pagination:**
    - Default: 50 items per page
    - Maximum: 100 items per page
    - Orders are sorted by creation date (newest first)
    - Returns pagination metadata with total count and page info
    
    **Response includes:**
    - Order summary information
    - List of tests in each order
    - Estimated completion times
    - Patient and doctor references
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
    
    result = await service.get_orders(
        page=page,
        limit=limit,
        status_filter=status,
        priority_filter=priority,
        date_from=date_from_dt,
        date_to=date_to_dt
    )
    return LabOrderListResponse(**result)


@router.get("/orders/{order_id}", response_model=LabOrderResponse)
async def get_lab_order(
    order_id: str,
    current_user: User = Depends(verify_lab_registration_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get detailed information about a specific lab order.
    
    **Access Control:**
    - Available to LAB_TECH role only
    - Hospital isolation enforced (can only access orders from own hospital)
    
    **Returns:**
    - Complete order information including all fields
    - List of all tests in the order with their details
    - Patient and doctor references
    - Order timeline and status information
    - Estimated completion time
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
    
    result = await service.get_order_by_id(order_uuid)
    return LabOrderResponse(**result)


@router.patch("/orders/{order_id}/priority", response_model=OrderPriorityUpdateResponse)
async def update_order_priority(
    order_id: str,
    priority_data: PriorityUpdateRequest,
    current_user: User = Depends(verify_lab_registration_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Update the priority of a lab order.
    
    **Access Control:**
    - Only users with LAB_TECH role can update priority
    - Hospital isolation enforced
    
    **Priority Levels:**
    - **ROUTINE**: Standard processing (default)
    - **URGENT**: High priority processing
    - **STAT**: Immediate/emergency processing
    
    **Business Rules:**
    - Priority can be changed at any time before completion
    - Reason for priority change is optional but recommended
    - Priority changes are logged for audit purposes
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
    
    result = await service.update_order_priority(
        order_uuid, 
        priority_data.priority, 
        priority_data.reason
    )
    return OrderPriorityUpdateResponse(**result)


@router.patch("/orders/{order_id}/cancel", response_model=OrderCancelResponse)
async def cancel_lab_order(
    order_id: str,
    cancel_data: CancelOrderRequest,
    current_user: User = Depends(verify_lab_registration_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Cancel a lab order and all its tests.
    
    **Access Control:**
    - Only users with LAB_TECH role can cancel orders
    - Hospital isolation enforced
    
    **Cancellation Rules:**
    - Orders can only be cancelled if not yet completed
    - Cancellation reason is required
    - All tests in the order are automatically cancelled
    - Cancelled orders cannot be reactivated
    
    **Business Rules:**
    - Cancellation timestamp is recorded
    - Cancellation reason and user are logged for audit
    - Order status changes to CANCELLED
    - All order items status change to CANCELLED
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
    
    result = await service.cancel_order(
        order_uuid,
        cancel_data.reason,
        cancel_data.cancelled_by,
    )
    return OrderCancelResponse(**result)


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@router.get("/sample-types", response_model=dict)
async def get_sample_types(
    current_user: User = Depends(verify_lab_tech_role)
):
    """
    Get list of available sample types.
    
    **Returns:**
    - All available sample types with their values
    - Useful for frontend dropdowns and filtering
    """
    return {
        "sample_types": [
            {"value": sample_type.value, "label": sample_type.value.replace("_", " ").title()}
            for sample_type in SampleType
        ]
    }


@router.get("/order-priorities", response_model=dict)
async def get_order_priorities(
    current_user: User = Depends(verify_lab_tech_role)
):
    """
    Get list of available order priorities.
    
    **Returns:**
    - All available priority levels with their values
    - Useful for frontend dropdowns and filtering
    """
    return {
        "priorities": [
            {"value": priority.value, "label": priority.value.replace("_", " ").title()}
            for priority in LabOrderPriority
        ]
    }


@router.get("/order-statuses", response_model=dict)
async def get_order_statuses(
    current_user: User = Depends(verify_lab_tech_role)
):
    """
    Get list of available order statuses.
    
    **Returns:**
    - All available order statuses with their values
    - Useful for frontend dropdowns and filtering
    """
    return {
        "statuses": [
            {"value": status.value, "label": status.value.replace("_", " ").title()}
            for status in LabOrderStatus
        ]
    }


@router.get("/stats", response_model=dict)
async def get_lab_registration_statistics(
    current_user: User = Depends(verify_lab_tech_role),
    service: LabService = Depends(get_lab_service)
):
    """
    Get lab registration statistics for the hospital.
    
    **Access Control:**
    - Available to LAB_TECH role only
    - Statistics are scoped to user's hospital
    
    **Returns:**
    - Total test count in catalogue
    - Total orders count
    - Orders by status breakdown
    - Orders by priority breakdown
    """
    stats = await service.get_registration_statistics()
    return stats