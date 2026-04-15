"""
Lab Test Registration Schemas
Pydantic models for lab test catalogue, orders, and order items.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from uuid import UUID

from app.core.enums import (
    SampleType, LabOrderSource, LabOrderPriority, LabOrderStatus, 
    LabTestStatus, LabOrderItemStatus, SampleStatus, ContainerType, RejectionReason, CollectionSite,
    ResultStatus, ResultFlag, EquipmentStatus, EquipmentCategory, MaintenanceType, 
    QCFrequency, QCStatus, QCRuleStatus, ViewerType, NotificationEventType, NotificationChannel
)


# ============================================================================
# LAB TEST CATALOGUE SCHEMAS
# ============================================================================

# --- Category ---
class CategoryCreateRequest(BaseModel):
    """Request schema for creating a lab test category/department"""
    category_code: str = Field(..., min_length=1, max_length=50, description="Unique category code (e.g., HEMA, BIO)")
    name: str = Field(..., min_length=2, max_length=255, description="Category name")
    description: Optional[str] = Field(None, max_length=1000)
    display_order: int = Field(0, ge=0, description="Display order")
    is_active: bool = Field(True, description="Whether category is active")

    @field_validator("category_code")
    @classmethod
    def validate_category_code(cls, v):
        return v.upper().strip()

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "category_code": "HEMA",
                "name": "Hematology",
                "description": "Blood and related tests",
                "display_order": 1,
                "is_active": True,
            }
        }
    )


class CategoryUpdateRequest(BaseModel):
    """Request schema for updating a lab test category"""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    display_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None

    model_config = ConfigDict(json_schema_extra={"example": {"name": "Hematology (updated)", "is_active": True}})


class CategoryResponse(BaseModel):
    """Response schema for lab test category"""
    category_id: UUID
    category_code: str
    name: str
    description: Optional[str]
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _utc_aware_timestamps(cls, v):
        if v is None:
            return v
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    model_config = ConfigDict(from_attributes=True)


class CategoryListResponse(BaseModel):
    """Response schema for paginated category list"""
    categories: List[CategoryResponse]
    pagination: Dict[str, Any]

    model_config = ConfigDict(
        json_schema_extra={"example": {"categories": [], "pagination": {"page": 1, "limit": 10, "total": 0, "pages": 0}}}
    )


class CategoryCreateResponse(BaseModel):
    """Response schema for category creation"""
    category_id: UUID
    category_code: str
    name: str
    message: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"category_id": "123e4567-e89b-12d3-a456-426614174000", "category_code": "HEMA", "name": "Hematology", "message": "Category created successfully"}
        }
    )


class CategoryUpdateResponse(BaseModel):
    """Response schema for category update"""
    category_id: UUID
    category_code: str
    name: str
    message: str

    model_config = ConfigDict(json_schema_extra={"example": {"category_id": "...", "category_code": "HEMA", "name": "Hematology", "message": "Category updated successfully"}})


# --- Test ---
class TestCreateRequest(BaseModel):
    """Request schema for creating a lab test"""
    test_code: str = Field(..., min_length=1, max_length=50, description="Unique test code (e.g., CBC, TSH)")
    test_name: str = Field(..., min_length=2, max_length=255, description="Full test name")
    category_id: Optional[UUID] = Field(None, description="Category/department UUID")
    sample_type: SampleType = Field(..., description="Specimen/sample type required")
    turnaround_time_hours: int = Field(24, ge=1, le=168, description="Expected turnaround time in hours")
    price: Optional[Decimal] = Field(None, ge=0, description="Test price (no billing integration)")
    unit: Optional[str] = Field(None, max_length=50, description="Result unit (e.g. g/dL, mg/L)")
    methodology: Optional[str] = Field(None, max_length=255, description="Methodology (e.g. Automated, ELISA)")
    description: Optional[str] = Field(None, max_length=1000, description="Test description")
    preparation_instructions: Optional[str] = Field(None, max_length=1000, description="Patient preparation instructions")
    reference_ranges: Optional[Dict[str, Any]] = Field(None, description="Normal ranges (optionally by gender/age)")
    is_active: bool = Field(True, description="Whether test is active")

    @field_validator("test_code")
    @classmethod
    def validate_test_code(cls, v):
        return v.upper().strip()

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "test_code": "CBC",
                "test_name": "Complete Blood Count",
                "sample_type": "BLOOD",
                "turnaround_time_hours": 6,
                "price": 350.00,
                "unit": "g/dL",
                "methodology": "Automated",
                "description": "Complete blood count with differential",
                "preparation_instructions": "Fasting not required",
                "is_active": True,
            }
        }
    )


class TestUpdateRequest(BaseModel):
    """Request schema for updating a lab test"""
    test_name: Optional[str] = Field(None, min_length=2, max_length=255)
    category_id: Optional[UUID] = None
    sample_type: Optional[SampleType] = None
    turnaround_time_hours: Optional[int] = Field(None, ge=1, le=168)
    price: Optional[Decimal] = Field(None, ge=0)
    unit: Optional[str] = Field(None, max_length=50)
    methodology: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    preparation_instructions: Optional[str] = Field(None, max_length=1000)
    reference_ranges: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None

    model_config = ConfigDict(
        json_schema_extra={"example": {"test_name": "Complete Blood Count - Updated", "price": 400.00, "is_active": True}}
    )


class TestResponse(BaseModel):
    """Response schema for lab test details"""
    test_id: UUID
    test_code: str
    test_name: str
    category_id: Optional[UUID] = None
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    sample_type: str
    turnaround_time_hours: int
    price: Optional[Decimal] = None
    unit: Optional[str] = None
    methodology: Optional[str] = None
    description: Optional[str] = None
    preparation_instructions: Optional[str] = None
    reference_ranges: Optional[Dict[str, Any]] = None
    status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _test_utc_aware_timestamps(cls, v):
        if v is None:
            return v
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    model_config = ConfigDict(from_attributes=True)


class TestListResponse(BaseModel):
    """Response schema for paginated test list"""
    tests: List[TestResponse]
    pagination: Dict[str, Any]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tests": [
                    {
                        "test_id": "123e4567-e89b-12d3-a456-426614174000",
                        "test_code": "CBC",
                        "test_name": "Complete Blood Count",
                        "sample_type": "BLOOD",
                        "turnaround_time_hours": 6,
                        "price": 350.00,
                        "status": "ACTIVE",
                        "is_active": True
                    }
                ],
                "pagination": {
                    "page": 1,
                    "limit": 10,
                    "total": 25,
                    "pages": 3
                }
            }
        }
    )


# ============================================================================
# LAB ORDER SCHEMAS
# ============================================================================

class OrderTestItem(BaseModel):
    """Individual test item in an order"""
    test_id: UUID = Field(..., description="UUID of the lab test")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "test_id": "123e4567-e89b-12d3-a456-426614174000"
            }
        }
    )


class OrderReference(BaseModel):
    """Optional reference information for orders"""
    encounter_id: Optional[str] = Field(None, description="Encounter/visit reference")
    prescription_id: Optional[str] = Field(None, description="Prescription reference")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "encounter_id": "ENC-7781",
                "prescription_id": "RX-9921"
            }
        }
    )


class OrderCreateRequest(BaseModel):
    """Request schema for creating a lab order"""
    patient_ref: str = Field(..., description="Patient reference (e.g. PAT-001)")
    source: LabOrderSource = Field(..., description="Order source")
    priority: LabOrderPriority = Field(LabOrderPriority.ROUTINE, description="Order priority")
    tests: List[OrderTestItem] = Field(..., min_length=1, description="List of tests to order")
    requested_by_doctor_ref: Optional[str] = Field(None, description="Doctor ref or name (required for DOCTOR source)")
    reference: Optional[OrderReference] = Field(None, description="Optional reference information")
    notes: Optional[str] = Field(None, max_length=1000, description="Order notes")
    special_instructions: Optional[str] = Field(None, max_length=1000, description="Special instructions")
    create_as_draft: bool = Field(False, description="If true, order is created as DRAFT; use register endpoint to submit")

    @field_validator('requested_by_doctor_ref')
    @classmethod
    def validate_doctor_required(cls, v, info):
        values = info.data
        if values.get('source') == LabOrderSource.DOCTOR and not v:
            raise ValueError('requested_by_doctor_ref is required when source is DOCTOR')
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "patient_ref": "PAT-10021",
                    "source": "WALKIN",
                    "priority": "ROUTINE",
                    "tests": [
                        {"test_id": "123e4567-e89b-12d3-a456-426614174000"},
                        {"test_id": "123e4567-e89b-12d3-a456-426614174001"}
                    ],
                    "notes": "Fever since 3 days"
                }
            ]
        }
    )


# Basic response schemas
class MessageResponse(BaseModel):
    """Generic message response"""
    message: str
    status: Optional[str] = "success"
    data: Optional[Dict[str, Any]] = None


class OrderItemResponse(BaseModel):
    """Response schema for order item"""
    test_id: UUID
    test_code: str
    test_name: str
    sample_type: str
    status: str
    price: Optional[Decimal]

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    """Response schema for lab order (refs/names for display)."""
    order_id: UUID
    order_ref: str
    patient_ref: str
    patient_name: str
    source: str
    priority: str
    status: str
    total_tests: int
    total_amount: Optional[Decimal]
    requested_by_doctor_ref: Optional[str] = None
    requested_by_doctor_name: Optional[str] = None
    notes: Optional[str]
    special_instructions: Optional[str]
    created_at: datetime
    updated_at: datetime
    tests: List[OrderItemResponse]

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _order_utc_aware_timestamps(cls, v):
        if v is None:
            return v
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    model_config = ConfigDict(from_attributes=True)


class OrderPriorityUpdateResponse(BaseModel):
    """Response for PATCH /orders/{id}/priority"""
    message: str
    lab_order_id: str
    lab_order_no: str
    priority: str
    reason: Optional[str] = None


class OrderCancelResponse(BaseModel):
    """Response for PATCH /orders/{id}/cancel"""
    message: str
    lab_order_id: str
    lab_order_no: str
    status: str
    cancellation_reason: str
    cancelled_by: Optional[str] = None


# Equipment schemas
class EquipmentCreateRequest(BaseModel):
    """Request schema for creating lab equipment"""
    equipment_code: str = Field(..., min_length=1, max_length=50, description="Unique equipment code")
    equipment_name: str = Field(..., min_length=2, max_length=255, description="Equipment name")
    category: str = Field(..., description="Equipment category (HEMATOLOGY, BIOCHEMISTRY, etc.)")
    manufacturer: Optional[str] = Field(None, max_length=100, description="Manufacturer name")
    model: Optional[str] = Field(None, max_length=100, description="Equipment model")
    serial_number: Optional[str] = Field(None, max_length=100, description="Serial number")
    location: Optional[str] = Field(None, max_length=100, description="Lab section/room location")
    installation_date: Optional[datetime] = Field(None, description="Installation date")
    next_calibration_due_at: Optional[datetime] = Field(None, description="Next calibration due date")
    notes: Optional[str] = Field(None, max_length=1000, description="Equipment notes")
    specifications: Optional[Dict[str, Any]] = Field(None, description="Technical specifications")

    @field_validator('equipment_code')
    @classmethod
    def validate_equipment_code(cls, v):
        return v.upper().strip()

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "equipment_code": "HEMA-001",
                "equipment_name": "Automated Hematology Analyzer",
                "category": "HEMATOLOGY",
                "manufacturer": "Sysmex Corporation",
                "model": "XN-1000",
                "serial_number": "SN123456789",
                "location": "Hematology Section",
                "notes": "Primary CBC analyzer"
            }
        }
    )


class TestCreateResponse(BaseModel):
    """Response schema for test creation"""
    test_id: UUID
    test_code: str
    test_name: str
    message: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "test_id": "123e4567-e89b-12d3-a456-426614174000",
                "test_code": "CBC",
                "test_name": "Complete Blood Count",
                "message": "Lab test created successfully"
            }
        }
    )


class TestUpdateResponse(BaseModel):
    """Response schema for test update"""
    test_id: UUID
    test_code: str
    test_name: str
    message: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "test_id": "123e4567-e89b-12d3-a456-426614174000",
                "test_code": "CBC",
                "test_name": "Complete Blood Count - Updated",
                "message": "Lab test updated successfully"
            }
        }
    )

# Additional schemas for sample collection
class SampleCreateRequest(BaseModel):
    """Request schema for creating samples"""
    order_id: UUID = Field(..., description="Lab order UUID")
    samples: List[Dict[str, Any]] = Field(..., min_length=1, description="List of samples to create")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "order_id": "123e4567-e89b-12d3-a456-426614174000",
                "samples": [
                    {
                        "test_id": "123e4567-e89b-12d3-a456-426614174001",
                        "container_type": "TUBE",
                        "volume_ml": 5.0
                    }
                ]
            }
        }
    )


class SampleCreateResponse(BaseModel):
    """Response for sample creation"""
    message: str
    samples_created: int
    sample_ids: List[UUID]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Samples created successfully",
                "samples_created": 2,
                "sample_ids": ["123e4567-e89b-12d3-a456-426614174000"]
            }
        }
    )


class SampleResponse(BaseModel):
    """Response schema for sample details"""
    sample_id: UUID
    order_ref: str = Field(default="", description="Order reference (lab_order_no)")
    test_name: str = Field(default="", description="Test name(s), comma-separated if multiple")
    sample_type: str
    container_type: str
    status: str
    collected_at: Optional[datetime] = None
    received_at: Optional[datetime] = Field(None, description="When sample was received in lab (received_in_lab_at)")

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def map_service_fields(cls, data: Any) -> Any:
        """Map lab_order_no -> order_ref, received_in_lab_at -> received_at, tests -> test_name"""
        if isinstance(data, dict):
            d = dict(data)
            if "order_ref" not in d and "lab_order_no" in d:
                d["order_ref"] = d["lab_order_no"]
            if "received_at" not in d and "received_in_lab_at" in d:
                d["received_at"] = d["received_in_lab_at"]
            if "test_name" not in d and "tests" in d:
                tests = d["tests"]
                d["test_name"] = ", ".join(t.get("test_name", "") for t in tests) if tests else ""
            return d
        return data


class SampleListResponse(BaseModel):
    """Response schema for sample list"""
    samples: List[SampleResponse]
    pagination: Dict[str, Any]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "samples": [],
                "pagination": {"page": 1, "limit": 10, "total": 0, "pages": 0}
            }
        }
    )


# Basic request schemas for other operations
class SampleCollectRequest(BaseModel):
    """Request for sample collection"""
    samples: List[Dict[str, Any]] = Field(..., min_length=1)

class SampleReceiveRequest(BaseModel):
    """Request for sample receiving"""
    samples: List[Dict[str, Any]] = Field(..., min_length=1)

class SampleRejectRequest(BaseModel):
    """Request for sample rejection"""
    rejection_reason: str = Field(..., min_length=5)
    rejection_notes: str = Field(..., min_length=5)


# Equipment schemas
class EquipmentUpdateRequest(BaseModel):
    """Request schema for updating equipment"""
    equipment_name: Optional[str] = Field(None, min_length=2, max_length=255)
    category: Optional[str] = None
    manufacturer: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    location: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=1000)


class EquipmentStatusUpdateRequest(BaseModel):
    """Request schema for equipment status update"""
    status: str = Field(..., description="New equipment status")
    notes: Optional[str] = Field(None, max_length=500)


class EquipmentResponse(BaseModel):
    """Response schema for equipment details"""
    equipment_id: UUID
    equipment_code: str
    equipment_name: str
    category: str
    status: str
    manufacturer: Optional[str]
    model: Optional[str]
    location: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EquipmentListResponse(BaseModel):
    """Response schema for equipment list"""
    equipment: List[EquipmentResponse]
    pagination: Dict[str, Any]


class MaintenanceLogCreateRequest(BaseModel):
    """Request for maintenance log creation"""
    equipment_id: UUID = Field(..., description="Equipment UUID")
    maintenance_type: str = Field(..., description="Type of maintenance")
    description: str = Field(..., min_length=5, max_length=1000)
    performed_by: str = Field(..., description="Who performed the maintenance")


# Result entry schemas
class ResultCreateRequest(BaseModel):
    """Request for result entry"""
    test_id: UUID = Field(..., description="Test UUID")
    values: List[Dict[str, Any]] = Field(..., min_length=1, description="Result values")


class ResultVerifyRequest(BaseModel):
    """Request for result verification"""
    verification_notes: Optional[str] = Field(None, max_length=1000)


class ResultReleaseRequest(BaseModel):
    """Request for result release"""
    release_notes: Optional[str] = Field(None, max_length=1000)


class ResultRejectRequest(BaseModel):
    """Request for result rejection"""
    rejection_reason: str = Field(..., min_length=5, max_length=1000)


class ResultApproveRequest(BaseModel):
    """Request for pathologist approval (digital signature placeholder)"""
    signature_placeholder: Optional[str] = Field(None, max_length=2000)


class TestResultResponse(BaseModel):
    """Response for test result"""
    result_id: UUID
    test_name: str
    status: str
    entered_at: Optional[datetime]
    verified_at: Optional[datetime]
    released_at: Optional[datetime]
    approved_at: Optional[datetime] = None
    approved_by: Optional[UUID] = None
    previous_result_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class WorklistResponse(BaseModel):
    """Response for worklist"""
    tests: List[TestResultResponse]
    pagination: Dict[str, Any]


class ReportGenerateRequest(BaseModel):
    """Request for report generation"""
    order_id: UUID = Field(..., description="Lab order UUID")
    template_id: Optional[str] = Field(None, description="Report template")


class LabReportResponse(BaseModel):
    """Response for lab report"""
    report_id: UUID
    order_ref: str
    patient_name: str
    generated_at: datetime
    status: str

    model_config = ConfigDict(from_attributes=True)


# Report access schemas
class DoctorReportListResponse(BaseModel):
    """Response for doctor report list"""
    reports: List[LabReportResponse]
    pagination: Dict[str, Any]


class PatientReportListResponse(BaseModel):
    """Response for patient report list"""
    reports: List[LabReportResponse]
    pagination: Dict[str, Any]


class ReportMetadataResponse(BaseModel):
    """Response for report metadata"""
    report_id: UUID
    metadata: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class ShareTokenCreateRequest(BaseModel):
    """Request for share token creation"""
    report_id: UUID = Field(..., description="Report UUID")
    expires_in_hours: int = Field(24, ge=1, le=168, description="Token expiry in hours")


class ShareTokenResponse(BaseModel):
    """Response for share token"""
    token: str
    expires_at: datetime
    share_url: str

    model_config = ConfigDict(from_attributes=True)


class ShareTokenAccessResponse(BaseModel):
    """Response for share token access"""
    report_id: UUID
    patient_name: str
    report_data: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


# Audit and compliance schemas
class AuditLogResponse(BaseModel):
    """Response for audit log"""
    log_id: UUID
    action: str
    resource_type: str
    performed_by: str
    performed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    """Response for audit log list"""
    logs: List[AuditLogResponse]
    pagination: Dict[str, Any]


class ChainOfCustodyResponse(BaseModel):
    """Response for chain of custody"""
    sample_id: UUID
    custody_events: List[Dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)


class SampleTraceResponse(BaseModel):
    """Response for sample trace"""
    sample_id: UUID
    trace_events: List[Dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)


class ComplianceExportRequest(BaseModel):
    """Request for compliance export"""
    date_from: datetime = Field(..., description="Export start date")
    date_to: datetime = Field(..., description="Export end date")
    export_type: str = Field(..., description="Type of export")


class ComplianceExportResponse(BaseModel):
    """Response for compliance export"""
    export_id: UUID
    download_url: str
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnalyticsTATResponse(BaseModel):
    """Response for analytics TAT"""
    average_tat_hours: float
    median_tat_hours: float
    breakdown: List[Dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)

# Lab Order schemas (aliases for compatibility)
LabOrderCreateRequest = OrderCreateRequest
LabOrderResponse = OrderResponse


class LabOrderCreateResponse(BaseModel):
    """Response for lab order creation"""
    order_id: UUID
    order_ref: str
    message: str
    total_tests: int
    status: Optional[str] = None  # DRAFT or REGISTERED

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "order_id": "123e4567-e89b-12d3-a456-426614174000",
                "order_ref": "ORD-2026-001",
                "message": "Lab order created successfully",
                "total_tests": 2,
                "status": "REGISTERED",
            }
        }
    )


class RegisterOrderResponse(BaseModel):
    """Response for registering a DRAFT order"""
    lab_order_id: str
    lab_order_no: str
    status: str
    message: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "lab_order_id": "123e4567-e89b-12d3-a456-426614174000",
                "lab_order_no": "LAB-2026-00001",
                "status": "REGISTERED",
                "message": "Order registered successfully",
            }
        }
    )


class LabOrderListResponse(BaseModel):
    """Response for lab order list"""
    orders: List[OrderResponse]
    pagination: Dict[str, Any]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "orders": [],
                "pagination": {"page": 1, "limit": 10, "total": 0, "pages": 0}
            }
        }
    )


class PriorityUpdateRequest(BaseModel):
    """Request for priority update"""
    priority: str = Field(..., description="New priority level")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for priority change")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "priority": "URGENT",
                "reason": "Patient condition deteriorated"
            }
        }
    )


class CancelOrderRequest(BaseModel):
    """Request for order cancellation"""
    reason: str = Field(..., min_length=5, max_length=500, description="Cancellation reason")
    cancelled_by: Optional[str] = Field(None, description="Who cancelled the order")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reason": "Patient discharged before sample collection",
                "cancelled_by": "Dr. Smith"
            }
        }
    )
# Additional sample collection schemas
class BulkCollectRequest(BaseModel):
    """Request for bulk sample collection"""
    samples: List[Dict[str, Any]] = Field(..., min_length=1, description="Samples to collect")
    collection_round: Optional[str] = Field(None, max_length=100, description="Collection round identifier")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "samples": [
                    {
                        "sample_id": "123e4567-e89b-12d3-a456-426614174000",
                        "volume_ml": 5.0,
                        "notes": "Collected successfully"
                    }
                ],
                "collection_round": "MORNING-ROUND-1"
            }
        }
    )


class BulkCollectResponse(BaseModel):
    """Response for bulk sample collection"""
    message: str
    samples_collected: int
    failed_collections: int
    collection_summary: Dict[str, Any]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Bulk collection completed",
                "samples_collected": 15,
                "failed_collections": 2,
                "collection_summary": {
                    "total_attempted": 17,
                    "success_rate": 88.2
                }
            }
        }
    )


class BarcodeResponse(BaseModel):
    """Response for barcode generation"""
    sample_id: UUID
    barcode: str
    barcode_url: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sample_id": "123e4567-e89b-12d3-a456-426614174000",
                "barcode": "LAB2026001234",
                "barcode_url": "/api/v1/lab/samples/123e4567-e89b-12d3-a456-426614174000/barcode.png"
            }
        }
    )
class ReportHistoryResponse(BaseModel):
    """Response for report history"""
    reports: List[LabReportResponse]
    pagination: Dict[str, Any]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reports": [],
                "pagination": {"page": 1, "limit": 10, "total": 0, "pages": 0}
            }
        }
    )
# Additional missing schemas for equipment QC and analytics
class MaintenanceLogResponse(BaseModel):
    """Response for maintenance log"""
    log_id: UUID
    equipment_code: str
    maintenance_type: str
    description: str
    performed_by: str
    performed_at: datetime
    status: str

    model_config = ConfigDict(from_attributes=True)


class MaintenanceLogListResponse(BaseModel):
    """Response for maintenance log list"""
    logs: List[MaintenanceLogResponse]
    pagination: Dict[str, Any]


class QCRuleCreateRequest(BaseModel):
    """Request for QC rule creation"""
    rule_name: str = Field(..., min_length=2, max_length=255)
    test_id: UUID = Field(..., description="Test UUID")
    rule_type: str = Field(..., description="Type of QC rule")
    parameters: Dict[str, Any] = Field(..., description="Rule parameters")


class QCRuleResponse(BaseModel):
    """Response for QC rule"""
    rule_id: UUID
    rule_name: str
    test_name: str
    rule_type: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QCRuleListResponse(BaseModel):
    """Response for QC rule list"""
    rules: List[QCRuleResponse]
    pagination: Dict[str, Any]


class QCRunCreateRequest(BaseModel):
    """Request for QC run creation"""
    equipment_id: UUID = Field(..., description="Equipment UUID")
    qc_type: str = Field(..., description="Type of QC run")
    parameters: Dict[str, Any] = Field(..., description="QC parameters")


class QCRunResponse(BaseModel):
    """Response for QC run"""
    run_id: UUID
    equipment_code: str
    qc_type: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime]
    results: Optional[Dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)


class QCRunListResponse(BaseModel):
    """Response for QC run list"""
    runs: List[QCRunResponse]
    pagination: Dict[str, Any]


class QCStatusResponse(BaseModel):
    """Response for QC status"""
    equipment_id: UUID
    equipment_code: str
    last_qc_run: Optional[datetime]
    qc_status: str
    next_qc_due: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class NotificationCreateRequest(BaseModel):
    """Request for notification creation"""
    recipient_id: str = Field(..., description="Recipient user ID")
    event_type: str = Field(..., description="Type of notification event")
    message: str = Field(..., min_length=1, max_length=1000)
    priority: str = Field("NORMAL", description="Notification priority")


class NotificationResponse(BaseModel):
    """Response for notification"""
    notification_id: UUID
    recipient_id: str
    event_type: str
    message: str
    priority: str
    status: str
    created_at: datetime
    read_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class NotificationStatusResponse(BaseModel):
    """Response for notification status update"""
    notification_id: UUID
    status: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnalyticsEquipmentResponse(BaseModel):
    """Response for equipment analytics"""
    equipment_id: UUID
    equipment_code: str
    utilization_rate: float
    uptime_percentage: float
    maintenance_frequency: int
    performance_metrics: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class AnalyticsQCResponse(BaseModel):
    """Response for QC analytics"""
    period: Dict[str, Any]
    qc_pass_rate: float
    total_qc_runs: int
    failed_qc_runs: int
    equipment_breakdown: List[Dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)


class AnalyticsVolumeResponse(BaseModel):
    """Response for volume analytics"""
    period: Dict[str, Any]
    total_tests: int
    daily_average: float
    peak_day: str
    test_breakdown: List[Dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)


class ReportSummaryResponse(BaseModel):
    """Response for report summary"""
    total_reports: int
    pending_reports: int
    completed_reports: int
    average_tat_hours: float
    breakdown_by_status: Dict[str, int]

    model_config = ConfigDict(from_attributes=True)