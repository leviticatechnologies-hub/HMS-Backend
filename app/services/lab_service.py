"""
Lab Test Registration Service
Handles lab test catalogue, orders, and order items with hospital isolation.
"""
import hashlib
import secrets
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, List, Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, update, desc, asc, delete, case
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException, status

from app.models.lab import LabTestCategory, LabTest, LabOrder, LabOrderItem, Sample, SampleOrderItem, TestResult, ResultValue, LabReport, Equipment, EquipmentMaintenanceLog, QCRule, QCRun, QCCorrectiveAction
from app.repositories.lab_repository import LabCatalogueRepository
from app.core.enums import (
    SampleType, LabOrderSource, LabOrderPriority, LabOrderStatus, 
    LabTestStatus, LabOrderItemStatus, SampleStatus, ContainerType, RejectionReason, CollectionSite,
    ResultStatus, ResultFlag, EquipmentStatus, EquipmentCategory, MaintenanceType, 
    QCFrequency, QCStatus, QCRuleStatus
)


def _validate_qc_values(rule: QCRule, values: Optional[Dict[str, Any]]) -> tuple:
    """
    Validate QC run values against rule min/max. Returns (status, deviation_notes).
    """
    if not values or (rule.min_value is None and rule.max_value is None):
        return (None, None)
    # Extract numeric value: rule.parameter_name, "value", or first numeric
    raw = values.get(rule.parameter_name) if rule.parameter_name else None
    if raw is None:
        raw = values.get("value")
    if raw is None:
        for v in values.values():
            if isinstance(v, (int, float)):
                raw = v
                break
    if raw is None:
        return (None, None)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return (QCStatus.FAIL.value, "Non-numeric value")
    low, high = rule.min_value, rule.max_value
    if low is not None and val < float(low):
        return (QCStatus.FAIL.value, f"Value {val} below minimum {low}")
    if high is not None and val > float(high):
        return (QCStatus.FAIL.value, f"Value {val} above maximum {high}")
    return (QCStatus.PASS.value, None)
from app.core.utils import generate_sample_number, generate_sample_barcode, ensure_datetime_utc_aware

# Order statuses from which cancellation is not allowed
_ORDER_CANCEL_FORBIDDEN = {
    LabOrderStatus.CANCELLED,
    LabOrderStatus.COMPLETED,
    LabOrderStatus.REPORTED,
    LabOrderStatus.APPROVED,
}
# Allowed transition: DRAFT -> REGISTERED only via register_order
_ORDER_REGISTER_FROM = {LabOrderStatus.DRAFT}

# Result statuses that are immutable; correction = new version with previous_result_id
_RESULT_IMMUTABLE_STATUSES = {ResultStatus.APPROVED, ResultStatus.RELEASED}


def _interpret_result_value(value_str: str, reference_range_str: Optional[str]) -> tuple:
    """
    Interpret a numeric result value against a reference range string (e.g. "12-16", "4.0-5.5").
    Returns (flag: ResultFlag or None, is_abnormal: bool).
    """
    if not reference_range_str or not value_str:
        return (None, False)
    try:
        value = float(value_str.replace(",", ".").strip())
    except (ValueError, TypeError):
        return (ResultFlag.ABNORMAL, True)
    ref = reference_range_str.strip()
    # Parse "min-max" or "min - max"
    if "-" in ref:
        parts = ref.replace(" ", "").split("-")
        if len(parts) >= 2:
            try:
                low = float(parts[0].replace(",", "."))
                high = float(parts[1].replace(",", "."))
                if value < low:
                    return (ResultFlag.LOW, True)
                if value > high:
                    return (ResultFlag.HIGH, True)
                return (ResultFlag.NORMAL, False)
            except (ValueError, TypeError):
                pass
    return (None, False)


class LabService:
    """Service class for lab test registration and management operations"""

    def __init__(self, db: AsyncSession, hospital_id: Optional[uuid.UUID] = None):
        self.db = db
        self.hospital_id = hospital_id
        self._catalogue_repo = LabCatalogueRepository(db, hospital_id) if hospital_id else None

    def _repo(self) -> LabCatalogueRepository:
        if not self._catalogue_repo or not self.hospital_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "NO_HOSPITAL_CONTEXT", "message": "Hospital context required"})
        return self._catalogue_repo

    # ============================================================================
    # LAB TEST CATEGORY OPERATIONS
    # ============================================================================

    async def create_category(self, category_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new lab test category (department)."""
        repo = self._repo()
        existing = await repo.get_category_by_code(category_data["category_code"])
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "DUPLICATE_CATEGORY_CODE", "message": f"Category code '{category_data['category_code']}' already exists"}
            )
        category = LabTestCategory(
            hospital_id=self.hospital_id,
            category_code=category_data["category_code"].upper().strip(),
            name=category_data["name"],
            description=category_data.get("description"),
            display_order=category_data.get("display_order", 0),
            is_active=category_data.get("is_active", True),
        )
        await repo.create_category(category)
        await self.db.commit()
        return {
            "category_id": category.id,
            "category_code": category.category_code,
            "name": category.name,
            "message": "Category created successfully",
        }

    async def get_category_by_id(self, category_id: uuid.UUID) -> Dict[str, Any]:
        """Get category by ID."""
        repo = self._repo()
        cat = await repo.get_category_by_id(category_id)
        if not cat:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "CATEGORY_NOT_FOUND", "message": "Category not found"})
        return {
            "category_id": cat.id,
            "category_code": cat.category_code,
            "name": cat.name,
            "description": cat.description,
            "display_order": cat.display_order,
            "is_active": cat.is_active,
            "created_at": ensure_datetime_utc_aware(cat.created_at),
            "updated_at": ensure_datetime_utc_aware(cat.updated_at),
        }

    async def list_categories(self, page: int = 1, limit: int = 50, active_only: bool = True) -> Dict[str, Any]:
        """List categories with pagination."""
        repo = self._repo()
        skip = (page - 1) * limit
        total = await repo.count_categories(active_only=active_only)
        categories = await repo.list_categories(active_only=active_only, skip=skip, limit=limit)
        total_pages = (total + limit - 1) // limit if total else 0
        return {
            "categories": [
                {
                    "category_id": c.id,
                    "category_code": c.category_code,
                    "name": c.name,
                    "description": c.description,
                    "display_order": c.display_order,
                    "is_active": c.is_active,
                    "created_at": ensure_datetime_utc_aware(c.created_at),
                    "updated_at": ensure_datetime_utc_aware(c.updated_at),
                }
                for c in categories
            ],
            "pagination": {"page": page, "limit": limit, "total": total, "pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1},
        }

    async def update_category(self, category_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update category."""
        repo = self._repo()
        cat = await repo.get_category_by_id(category_id)
        if not cat:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "CATEGORY_NOT_FOUND", "message": "Category not found"})
        for k, v in update_data.items():
            if v is not None and hasattr(cat, k):
                setattr(cat, k, v)
        await repo.update_category(cat)
        await self.db.commit()
        return {"category_id": cat.id, "category_code": cat.category_code, "name": cat.name, "message": "Category updated successfully"}

    # ============================================================================
    # LAB TEST CATALOGUE OPERATIONS
    # ============================================================================

    def _test_to_response(self, test: LabTest) -> Dict[str, Any]:
        """Build test response dict including category and new fields."""
        cat = getattr(test, "category", None)
        return {
            "test_id": test.id,
            "test_code": test.test_code,
            "test_name": test.test_name,
            "category_id": test.category_id,
            "category_code": cat.category_code if cat else None,
            "category_name": cat.name if cat else None,
            "sample_type": test.sample_type,
            "turnaround_time_hours": test.turnaround_time_hours,
            "price": test.price,
            "unit": getattr(test, "unit", None),
            "methodology": getattr(test, "methodology", None),
            "description": test.description,
            "preparation_instructions": test.preparation_instructions,
            "reference_ranges": test.reference_ranges,
            "status": test.status,
            "is_active": test.is_active,
            "created_at": ensure_datetime_utc_aware(test.created_at),
            "updated_at": ensure_datetime_utc_aware(test.updated_at),
        }

    async def create_test(self, test_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new lab test in the catalogue. Validates category_id if provided."""
        repo = self._repo()
        if await repo.get_test_by_code(test_data["test_code"]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "DUPLICATE_TEST_CODE", "message": f"Test code '{test_data['test_code']}' already exists in this hospital"}
            )
        category_id = test_data.get("category_id")
        if category_id:
            cat = await repo.get_category_by_id(category_id)
            if not cat:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "CATEGORY_NOT_FOUND", "message": "Category not found"})
        test = LabTest(
            hospital_id=self.hospital_id,
            category_id=category_id,
            test_code=test_data["test_code"].upper().strip(),
            test_name=test_data["test_name"],
            sample_type=test_data["sample_type"].value,
            turnaround_time_hours=test_data.get("turnaround_time_hours", 24),
            price=test_data.get("price"),
            unit=test_data.get("unit"),
            methodology=test_data.get("methodology"),
            description=test_data.get("description"),
            preparation_instructions=test_data.get("preparation_instructions"),
            reference_ranges=test_data.get("reference_ranges") or {},
            status=LabTestStatus.ACTIVE,
            is_active=test_data.get("is_active", True),
        )
        await repo.create_test(test)
        await self.db.commit()
        return {"test_id": test.id, "test_code": test.test_code, "test_name": test.test_name, "message": "Lab test created successfully"}

    async def get_tests(
        self,
        page: int = 1,
        limit: int = 50,
        active_only: bool = True,
        sample_type_filter: Optional[SampleType] = None,
        category_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated list of lab tests with filters and search."""
        repo = self._repo()
        skip = (page - 1) * limit
        sample_type_str = sample_type_filter.value if sample_type_filter else None
        total = await repo.count_tests(active_only=active_only, category_id=category_id, sample_type=sample_type_str, search=search)
        tests = await repo.list_tests(active_only=active_only, category_id=category_id, sample_type=sample_type_str, search=search, skip=skip, limit=limit)
        total_pages = (total + limit - 1) // limit if total else 0
        test_responses = []
        for test in tests:
            test_responses.append(self._test_to_response(test))
        return {
            "tests": test_responses,
            "pagination": {"page": page, "limit": limit, "total": total, "pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1},
        }

    async def get_test_by_id(self, test_id: uuid.UUID) -> Dict[str, Any]:
        """Get a single test by ID with hospital isolation."""
        repo = self._repo()
        test = await repo.get_test_by_id(test_id)
        if not test:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "TEST_NOT_FOUND", "message": f"Test with ID {test_id} not found"})
        return self._test_to_response(test)

    async def update_test(self, test_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update test; validates category_id if provided."""
        repo = self._repo()
        test = await repo.get_test_by_id(test_id)
        if not test:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "TEST_NOT_FOUND", "message": f"Test with ID {test_id} not found"})
        category_id = update_data.get("category_id")
        if category_id is not None:
            if category_id:
                cat = await repo.get_category_by_id(category_id)
                if not cat:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "CATEGORY_NOT_FOUND", "message": "Category not found"})
            test.category_id = category_id
        allowed = {"test_name", "category_id", "sample_type", "turnaround_time_hours", "price", "unit", "methodology", "description", "preparation_instructions", "reference_ranges", "is_active"}
        for field, value in update_data.items():
            if field in allowed and value is not None:
                if field == "sample_type":
                    setattr(test, field, value.value if hasattr(value, "value") else value)
                else:
                    setattr(test, field, value)
        await repo.update_test(test)
        await self.db.commit()
        return {"test_id": test_id, "test_code": test.test_code, "test_name": test.test_name, "message": "Lab test updated successfully"}
    
    # ============================================================================
    # LAB ORDER OPERATIONS
    # ============================================================================
    
    async def create_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new lab order with tests.
        
        Args:
            order_data: Dictionary containing order information
            
        Returns:
            Dictionary with creation result and order details
            
        Raises:
            HTTPException: If validation fails or tests not found
        """
        try:
            # Generate unique lab order number
            lab_order_no = await self._generate_lab_order_number()

            def _row_test_id(row: Any) -> uuid.UUID:
                if isinstance(row, dict):
                    tid = row.get("test_id")
                else:
                    tid = getattr(row, "test_id", None)
                if tid is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"code": "INVALID_TESTS", "message": "Each test row must include test_id"},
                    )
                return tid if isinstance(tid, uuid.UUID) else uuid.UUID(str(tid))

            def _row_sample_type(row: Any):
                if isinstance(row, dict):
                    return row.get("sample_type")
                return getattr(row, "sample_type", None)

            # Validate tests exist
            test_ids = [_row_test_id(item) for item in order_data["tests"]]
            tests_result = await self.db.execute(
                select(LabTest).where(
                    and_(
                        LabTest.id.in_(test_ids),
                        LabTest.hospital_id == self.hospital_id,
                        LabTest.is_active == True
                    )
                )
            )
            
            tests = tests_result.scalars().all()
            if len(tests) != len(test_ids):
                found_ids = {test.id for test in tests}
                missing_ids = [tid for tid in test_ids if tid not in found_ids]
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_TESTS",
                        "message": f"Tests not found or inactive: {missing_ids}"
                    }
                )

            test_by_id = {t.id: t for t in tests}
            for row in order_data["tests"]:
                st = _row_sample_type(row)
                if st is None:
                    continue
                tid = _row_test_id(row)
                catalog = test_by_id.get(tid)
                if not catalog:
                    continue
                st_val = st.value if hasattr(st, "value") else str(st)
                if str(st_val).strip().upper() != str(catalog.sample_type).strip().upper():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "SAMPLE_TYPE_MISMATCH",
                            "message": (
                                f"Sample type for test {tid} must match catalogue "
                                f"({catalog.sample_type}); got {st_val}"
                            ),
                        },
                    )
            
            create_as_draft = order_data.get("create_as_draft", False)
            initial_status = LabOrderStatus.DRAFT if create_as_draft else LabOrderStatus.REGISTERED
            initial_item_status = LabOrderItemStatus.DRAFT if create_as_draft else LabOrderItemStatus.REGISTERED

            # Create lab order (API uses patient_ref / requested_by_doctor_ref; DB columns stay patient_id / requested_by_doctor_id)
            patient_ref = order_data.get("patient_ref") or order_data.get("patient_id")
            doctor_ref = (
                order_data.get("requested_by_doctor_ref")
                or order_data.get("referring_doctor")
                or order_data.get("requested_by_doctor_id")
            )
            if not patient_ref:
                raise HTTPException(status_code=400, detail={"code": "MISSING_PATIENT_REF", "message": "patient_ref is required"})

            src = order_data.get("source", LabOrderSource.WALKIN)
            src_val = src.value if hasattr(src, "value") else str(src)

            pri = order_data.get("priority", LabOrderPriority.ROUTINE)
            pri_val = pri.value if hasattr(pri, "value") else str(pri)

            portal_lines = []
            for label, key in [
                ("Patient name", "patient_name"),
                ("Age", "age"),
                ("Gender", "gender"),
                ("Phone", "phone"),
                ("Email", "email"),
                ("Registration date", "registration_date"),
            ]:
                v = order_data.get(key)
                if v is not None and str(v).strip() != "":
                    portal_lines.append(f"{label}: {v}")
            merged_notes = order_data.get("notes")
            if portal_lines:
                block = "[Portal registration]\n" + "\n".join(portal_lines)
                merged_notes = f"{block}\n\n{merged_notes}" if merged_notes else block

            order = LabOrder(
                hospital_id=self.hospital_id,
                lab_order_no=lab_order_no,
                patient_id=patient_ref,
                requested_by_doctor_id=doctor_ref,
                source=src_val,
                priority=pri_val,
                status=initial_status,
                encounter_id=(order_data.get("reference") or {}).get("encounter_id"),
                prescription_id=(order_data.get("reference") or {}).get("prescription_id"),
                notes=merged_notes,
                special_instructions=order_data.get("special_instructions"),
            )

            self.db.add(order)
            await self.db.flush()  # Get the order ID

            # Create order items
            order_items = []
            for test_item in order_data["tests"]:
                item = LabOrderItem(
                    lab_order_id=order.id,
                    test_id=test_item["test_id"],
                    status=initial_item_status,
                )
                order_items.append(item)
                self.db.add(item)
            
            await self.db.commit()
            
            # ── AUTO-BILLING INTEGRATION ──────────────────────────────────────
            # Push lab test charges into the patient's active DRAFT bill.
            # This runs after commit() so the order record is safely persisted
            # even if billing push fails.
            try:
                await self._push_lab_order_to_bill(order, tests)
                await self.db.commit()
            except Exception as billing_ex:
                import logging as _log
                _log.getLogger(__name__).error(
                    f"[LAB→BILLING] Lab order {order.id} created but auto-bill push FAILED: {billing_ex}. "
                    "Billing staff must add lab charges manually."
                )

            # Calculate estimated completion time
            max_turnaround = max(test.turnaround_time_hours for test in tests)
            estimated_completion = datetime.utcnow() + timedelta(hours=max_turnaround)
            
            return {
                "lab_order_id": order.id,
                "lab_order_no": order.lab_order_no,
                "patient_ref": order.patient_id,
                "patient_id": order.patient_id,
                "source": order.source,
                "priority": order.priority,
                "status": order.status,
                "total_tests": len(order_items),
                "estimated_completion": estimated_completion,
                "created_at": order.created_at,
                "message": "Lab order created successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "ORDER_CREATION_FAILED",
                    "message": f"Failed to create lab order: {str(e)}"
                }
            )

    async def _push_lab_order_to_bill(self, order, tests):
        """
        Auto-push lab test charges into the patient's active DRAFT bill.

        FIX: Previously lab orders had ZERO billing integration — tests were
        registered but revenue never appeared in the billing system.
        """
        import logging
        _logger = logging.getLogger(__name__)
        from app.models.billing.bill import Bill, BillItem
        from sqlalchemy import select, and_

        bill_result = await self.db.execute(
            select(Bill).where(
                and_(
                    Bill.hospital_id == self.hospital_id,
                    Bill.patient_id == order.patient_id,
                    Bill.status == "DRAFT",
                )
            ).order_by(Bill.created_at.desc()).limit(1)
        )
        bill = bill_result.scalar_one_or_none()

        if not bill:
            _logger.warning(
                f"[LAB→BILLING] No DRAFT bill found for patient {order.patient_id}. "
                f"Lab order {order.id} charges must be added manually."
            )
            return

        # Map tests by id for price lookup
        test_map = {t.id: t for t in tests}

        for order_item in order.lab_order_items if hasattr(order, 'lab_order_items') else []:
            test = test_map.get(order_item.test_id)
            if not test:
                continue
            price = float(getattr(test, 'price', 0) or 0)
            description = f"Lab: {test.name} ({order.lab_order_no})"
            bill_item = BillItem(
                bill_id=bill.id,
                service_item_id=None,
                description=description,
                quantity=1,
                unit_price=price,
                tax_percentage=0,
                line_subtotal=price,
                line_tax=0,
                line_total=price,
            )
            self.db.add(bill_item)

        # Fallback: if tests list provided directly (no relationship loaded)
        if not (hasattr(order, 'lab_order_items') and order.lab_order_items):
            for test in tests:
                price = float(getattr(test, 'price', 0) or 0)
                description = f"Lab: {test.name} ({order.lab_order_no})"
                bill_item = BillItem(
                    bill_id=bill.id,
                    service_item_id=None,
                    description=description,
                    quantity=1,
                    unit_price=price,
                    tax_percentage=0,
                    line_subtotal=price,
                    line_tax=0,
                    line_total=price,
                )
                self.db.add(bill_item)

        await self.db.flush()
        await self.db.refresh(bill)

        # Recalculate bill totals
        all_items_result = await self.db.execute(
            select(BillItem).where(BillItem.bill_id == bill.id)
        )
        all_items = all_items_result.scalars().all()
        subtotal = sum(float(i.line_subtotal) for i in all_items)
        tax = sum(float(i.line_tax) for i in all_items)
        total = sum(float(i.line_total) for i in all_items)
        bill.subtotal = subtotal
        bill.tax_amount = tax
        bill.total_amount = total - float(bill.discount_amount or 0)
        bill.balance_due = float(bill.total_amount) - float(bill.amount_paid or 0)
        await self.db.flush()

        _logger.info(
            f"[LAB→BILLING] Auto-added {len(tests)} lab test charge(s) to bill "
            f"{bill.bill_number} for patient {order.patient_id}."
        )
    
    async def get_orders(
        self, 
        page: int = 1, 
        limit: int = 50,
        status_filter: Optional[LabOrderStatus] = None,
        priority_filter: Optional[LabOrderPriority] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get paginated list of lab orders with filtering options.
        
        Args:
            page: Page number (1-based)
            limit: Items per page
            status_filter: Filter by order status
            priority_filter: Filter by priority
            date_from: Filter orders from this date
            date_to: Filter orders to this date
            
        Returns:
            Dictionary with orders and pagination info
        """
        try:
            if date_from is not None:
                date_from = ensure_datetime_utc_aware(date_from)
            if date_to is not None:
                date_to = ensure_datetime_utc_aware(date_to)

            # Build query conditions
            conditions = [LabOrder.hospital_id == self.hospital_id]
            
            if status_filter:
                conditions.append(LabOrder.status == status_filter.value)
            
            if priority_filter:
                conditions.append(LabOrder.priority == priority_filter.value)
            
            if date_from:
                conditions.append(LabOrder.created_at >= date_from)

            # date_to from API is YYYY-MM-DD parsed as midnight UTC — include the whole calendar day
            if date_to:
                end_exclusive = date_to + timedelta(days=1)
                conditions.append(LabOrder.created_at < end_exclusive)
            
            # Get total count
            count_query = select(func.count(LabOrder.id)).where(and_(*conditions))
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Calculate pagination
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit
            
            # Get orders with order items
            orders_query = (
                select(LabOrder)
                .where(and_(*conditions))
                .order_by(desc(LabOrder.created_at))
                .offset(offset)
                .limit(limit)
            )
            
            orders_result = await self.db.execute(orders_query)
            orders = orders_result.scalars().all()
            
            # Convert to response format
            order_responses = []
            for order in orders:
                # Get order items with test details
                items_result = await self.db.execute(
                    select(LabOrderItem, LabTest)
                    .join(LabTest, LabOrderItem.test_id == LabTest.id)
                    .where(LabOrderItem.lab_order_id == order.id)
                )
                
                items_data = items_result.all()
                tests = []
                total_amount = Decimal("0")
                for item, test in items_data:
                    tests.append({
                        "test_id": test.id,
                        "test_code": test.test_code,
                        "test_name": test.test_name,
                        "sample_type": test.sample_type,
                        "status": item.status,
                        "price": test.price
                    })
                    if test.price is not None:
                        total_amount += test.price
                
                # Calculate estimated completion
                if tests:
                    max_turnaround = max(test.turnaround_time_hours for _, test in items_data)
                    estimated_completion = order.created_at + timedelta(hours=max_turnaround)
                else:
                    estimated_completion = None
                
                pid = order.patient_id or ""
                doc_ref = order.requested_by_doctor_id
                order_responses.append({
                    "order_id": order.id,
                    "order_ref": order.lab_order_no,
                    "patient_ref": pid,
                    "patient_name": pid,
                    "source": order.source,
                    "priority": order.priority,
                    "status": order.status,
                    "total_tests": len(tests),
                    "total_amount": total_amount if total_amount > 0 else None,
                    "requested_by_doctor_ref": doc_ref,
                    "requested_by_doctor_name": doc_ref,
                    "notes": order.notes,
                    "special_instructions": order.special_instructions,
                    "created_at": ensure_datetime_utc_aware(order.created_at),
                    "updated_at": ensure_datetime_utc_aware(order.updated_at),
                    "tests": tests,
                })
            
            return {
                "orders": order_responses,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch orders: {str(e)}"
                }
            )
    
    async def get_order_by_id(self, order_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get a single order by ID with hospital isolation.
        
        Args:
            order_id: UUID of the order
            
        Returns:
            Dictionary with order details including tests
            
        Raises:
            HTTPException: If order not found
        """
        try:
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Get order items with test details
            items_result = await self.db.execute(
                select(LabOrderItem, LabTest)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(LabOrderItem.lab_order_id == order.id)
            )
            
            items_data = items_result.all()
            tests = []
            total_amount = Decimal("0")
            for item, test in items_data:
                tests.append({
                    "test_id": test.id,
                    "test_code": test.test_code,
                    "test_name": test.test_name,
                    "sample_type": test.sample_type,
                    "status": item.status,
                    "price": test.price
                })
                if test.price is not None:
                    total_amount += test.price
            
            pid = order.patient_id or ""
            doc_ref = order.requested_by_doctor_id
            return {
                "order_id": order.id,
                "order_ref": order.lab_order_no,
                "patient_ref": pid,
                "patient_name": pid,
                "source": order.source,
                "priority": order.priority,
                "status": order.status,
                "total_tests": len(tests),
                "total_amount": total_amount if total_amount > 0 else None,
                "requested_by_doctor_ref": doc_ref,
                "requested_by_doctor_name": doc_ref,
                "notes": order.notes,
                "special_instructions": order.special_instructions,
                "created_at": ensure_datetime_utc_aware(order.created_at),
                "updated_at": ensure_datetime_utc_aware(order.updated_at),
                "tests": tests,
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch order: {str(e)}"
                }
            )
    
    async def update_order_priority(
        self, 
        order_id: uuid.UUID, 
        new_priority: Union[str, LabOrderPriority],
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update order priority.
        
        Args:
            order_id: UUID of the order
            new_priority: New priority level (string e.g. "URGENT" or LabOrderPriority enum)
            reason: Reason for priority change
            
        Returns:
            Dictionary with update result
            
        Raises:
            HTTPException: If order not found
        """
        try:
            # Normalize to string: API may send str, model stores str
            priority_val = new_priority.value if isinstance(new_priority, LabOrderPriority) else new_priority
            # Validate against enum
            try:
                LabOrderPriority(priority_val)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_PRIORITY",
                        "message": f"Invalid priority. Must be one of: {[p.value for p in LabOrderPriority]}"
                    }
                )
            
            # Verify order exists and belongs to hospital
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Update priority
            await self.db.execute(
                update(LabOrder)
                .where(LabOrder.id == order_id)
                .values(priority=priority_val)
            )
            
            await self.db.commit()
            
            return {
                "message": f"Order priority updated to {priority_val}",
                "lab_order_id": str(order_id),
                "lab_order_no": order.lab_order_no,
                "priority": priority_val,
                "reason": reason
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "PRIORITY_UPDATE_FAILED",
                    "message": f"Failed to update order priority: {str(e)}"
                }
            )
    
    async def cancel_order(
        self, 
        order_id: uuid.UUID, 
        cancellation_reason: str,
        cancelled_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancel a lab order.
        
        Args:
            order_id: UUID of the order
            cancellation_reason: Reason for cancellation
            cancelled_by: Who cancelled the order
            
        Returns:
            Dictionary with cancellation result
            
        Raises:
            HTTPException: If order not found or cannot be cancelled
        """
        try:
            # Verify order exists and belongs to hospital
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Validate status transition: only non-final statuses can be cancelled
            try:
                current_status = LabOrderStatus(order.status) if isinstance(order.status, str) else order.status
            except ValueError:
                current_status = None
            if current_status in _ORDER_CANCEL_FORBIDDEN:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "CANNOT_CANCEL_ORDER",
                        "message": f"Cannot cancel order with status {order.status}. Only DRAFT, REGISTERED, SAMPLE_COLLECTED, IN_PROCESS, RESULT_ENTERED can be cancelled.",
                    },
                )
            
            # Cancel order and all items
            await self.db.execute(
                update(LabOrder)
                .where(LabOrder.id == order_id)
                .values(
                    status=LabOrderStatus.CANCELLED,
                    cancelled_at=datetime.utcnow(),
                    cancellation_reason=cancellation_reason,
                    cancelled_by=cancelled_by
                )
            )
            
            await self.db.execute(
                update(LabOrderItem)
                .where(LabOrderItem.lab_order_id == order_id)
                .values(status=LabOrderItemStatus.CANCELLED)
            )
            
            await self.db.commit()
            
            return {
                "message": "Lab order cancelled successfully",
                "lab_order_id": str(order_id),
                "lab_order_no": order.lab_order_no,
                "status": LabOrderStatus.CANCELLED.value,
                "cancellation_reason": cancellation_reason,
                "cancelled_by": cancelled_by,
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "CANCELLATION_FAILED",
                    "message": f"Failed to cancel order: {str(e)}"
                }
            )

    async def register_order(self, order_id: uuid.UUID) -> Dict[str, Any]:
        """
        Transition order from DRAFT to REGISTERED (submit order).
        Only DRAFT orders can be registered.
        """
        order_result = await self.db.execute(
            select(LabOrder).where(
                and_(
                    LabOrder.id == order_id,
                    LabOrder.hospital_id == self.hospital_id,
                )
            )
        )
        order = order_result.scalar_one_or_none()
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ORDER_NOT_FOUND", "message": f"Order with ID {order_id} not found"},
            )
        try:
            current_status = LabOrderStatus(order.status) if isinstance(order.status, str) else order.status
        except ValueError:
            current_status = None
        # Idempotent: if already REGISTERED, return success (no-op)
        if current_status == LabOrderStatus.REGISTERED:
            return {
                "lab_order_id": str(order_id),
                "lab_order_no": order.lab_order_no,
                "status": LabOrderStatus.REGISTERED.value,
                "message": "Order is already registered",
            }
        if current_status not in _ORDER_REGISTER_FROM:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_STATUS_TRANSITION",
                    "message": f"Only DRAFT orders can be registered. Current status: {order.status}",
                },
            )
        await self.db.execute(
            update(LabOrder).where(LabOrder.id == order_id).values(status=LabOrderStatus.REGISTERED)
        )
        await self.db.execute(
            update(LabOrderItem)
            .where(LabOrderItem.lab_order_id == order_id)
            .values(status=LabOrderItemStatus.REGISTERED)
        )
        await self.db.commit()
        return {
            "lab_order_id": str(order_id),
            "lab_order_no": order.lab_order_no,
            "status": LabOrderStatus.REGISTERED.value,
            "message": "Order registered successfully",
        }

    # ============================================================================
    # UTILITY METHODS
    # ============================================================================

    async def _generate_lab_order_number(self) -> str:
        """
        Generate unique lab order number in format LAB-YYYY-NNNNN.
        
        Returns:
            Unique lab order number
        """
        current_year = datetime.utcnow().year
        
        # Get the latest order number for this year and hospital
        latest_result = await self.db.execute(
            select(LabOrder.lab_order_no)
            .where(
                and_(
                    LabOrder.hospital_id == self.hospital_id,
                    LabOrder.lab_order_no.like(f"LAB-{current_year}-%")
                )
            )
            .order_by(desc(LabOrder.lab_order_no))
            .limit(1)
        )
        
        latest_order_no = latest_result.scalar_one_or_none()
        
        if latest_order_no:
            # Extract the sequence number and increment
            sequence_part = latest_order_no.split('-')[-1]
            next_sequence = int(sequence_part) + 1
        else:
            # First order of the year
            next_sequence = 1
        
        # Format with zero padding
        return f"LAB-{current_year}-{next_sequence:05d}"
    
    # ============================================================================
    # SAMPLE COLLECTION OPERATIONS
    # ============================================================================
    
    async def create_samples_for_order(
        self, 
        order_id: uuid.UUID, 
        samples_data: List[Dict[str, Any]],
        created_by: str
    ) -> Dict[str, Any]:
        """
        Create samples for a lab order.
        
        Args:
            order_id: UUID of the lab order
            samples_data: List of sample creation data
            created_by: User ID who is creating samples
            
        Returns:
            Dictionary with creation results
            
        Raises:
            HTTPException: If order not found or validation fails
        """
        try:
            # Verify order exists and belongs to hospital
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Get order items to validate test IDs
            order_items_result = await self.db.execute(
                select(LabOrderItem, LabTest)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(LabOrderItem.lab_order_id == order_id)
            )
            
            order_items_data = order_items_result.all()
            order_items_map = {item.id: (item, test) for item, test in order_items_data}
            
            # Start sample_sequence after any existing samples for this order (avoid barcode duplicate)
            sample_sequence = await self._get_next_sample_sequence_for_order(order_id, order.lab_order_no)
            
            created_samples = []
            
            for sample_data in samples_data:
                # Validate order item IDs - support lab_order_item_ids or test_id (auto-resolve)
                item_ids = sample_data.get('lab_order_item_ids')
                if item_ids is None:
                    # Support test_id: look up order item for this test in the order
                    test_id = sample_data.get('test_id')
                    if test_id is not None:
                        item_ids = [
                            item.id for item, _ in order_items_data
                            if str(item.test_id) == str(test_id)
                        ]
                        if not item_ids:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail={
                                    "code": "TEST_NOT_IN_ORDER",
                                    "message": f"Test {test_id} not found in this order"
                                }
                            )
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={
                                "code": "MISSING_SAMPLE_IDS",
                                "message": "Each sample must have 'lab_order_item_ids' or 'test_id'"
                            }
                        )
                valid_items = []
                for item_id in item_ids:
                    item_id_uuid = uuid.UUID(str(item_id)) if not isinstance(item_id, uuid.UUID) else item_id
                    if item_id_uuid not in order_items_map:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={
                                "code": "INVALID_ORDER_ITEM",
                                "message": f"Order item {item_id} not found in this order"
                            }
                        )
                    
                    item, test = order_items_map[item_id_uuid]
                    valid_items.append((item, test))
                
                # Derive sample_type from test if not provided (when using test_id)
                sample_type_val = sample_data.get('sample_type')
                if sample_type_val is not None:
                    sample_type_str = sample_type_val.value if hasattr(sample_type_val, 'value') else sample_type_val
                    for item, test in valid_items:
                        if test.sample_type != sample_type_str:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail={
                                    "code": "SAMPLE_TYPE_MISMATCH",
                                    "message": f"Test {test.test_code} requires {test.sample_type} sample, not {sample_type_str}"
                                }
                            )
                else:
                    sample_type_str = valid_items[0][1].sample_type
                
                # Generate sample identifiers
                sample_no = await self._generate_sample_number()
                barcode_value = generate_sample_barcode(order.lab_order_no, sample_sequence)
                
                # Create sample
                container_type_val = sample_data.get('container_type', ContainerType.PLAIN)
                container_type_str = container_type_val.value if hasattr(container_type_val, 'value') else (container_type_val or 'PLAIN')
                sample = Sample(
                    hospital_id=self.hospital_id,
                    sample_no=sample_no,
                    barcode_value=barcode_value,
                    qr_value=barcode_value,  # Use same value for QR
                    lab_order_id=order_id,
                    patient_id=order.patient_id,
                    sample_type=sample_type_str,
                    container_type=container_type_str,
                    status=SampleStatus.REGISTERED,
                    volume_ml=sample_data.get('volume_ml'),
                    notes=sample_data.get('notes')
                )
                
                self.db.add(sample)
                await self.db.flush()  # Get sample ID
                
                # Create sample-order item mappings
                for item, test in valid_items:
                    sample_item = SampleOrderItem(
                        sample_id=sample.id,
                        lab_order_item_id=item.id
                    )
                    self.db.add(sample_item)
                
                created_samples.append({
                    "sample_id": sample.id,
                    "sample_no": sample.sample_no,
                    "barcode_value": sample.barcode_value,
                    "sample_type": sample.sample_type,
                    "tests_count": len(valid_items)
                })
                
                sample_sequence += 1
            
            await self.db.commit()
            
            return {
                "message": "Samples created successfully",
                "samples_created": len(created_samples),
                "sample_ids": [s["sample_id"] for s in created_samples]
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "SAMPLE_CREATION_FAILED",
                    "message": f"Failed to create samples: {str(e)}"
                }
            )
    
    async def get_samples_for_order(self, order_id: uuid.UUID) -> List[Dict[str, Any]]:
        """
        Get all samples for a specific order.
        
        Args:
            order_id: UUID of the lab order
            
        Returns:
            List of sample details
        """
        try:
            # Verify order exists and belongs to hospital
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Get samples with their tests
            samples_result = await self.db.execute(
                select(Sample).where(Sample.lab_order_id == order_id)
            )
            
            samples = samples_result.scalars().all()
            sample_responses = []
            
            for sample in samples:
                # Get associated tests
                tests_result = await self.db.execute(
                    select(SampleOrderItem, LabOrderItem, LabTest)
                    .join(LabOrderItem, SampleOrderItem.lab_order_item_id == LabOrderItem.id)
                    .join(LabTest, LabOrderItem.test_id == LabTest.id)
                    .where(SampleOrderItem.sample_id == sample.id)
                )
                
                tests_data = tests_result.all()
                tests = []
                for _, item, test in tests_data:
                    tests.append({
                        "order_item_id": item.id,
                        "test_id": test.id,
                        "test_code": test.test_code,
                        "test_name": test.test_name,
                        "test_status": item.status
                    })
                
                sample_responses.append({
                    "sample_id": sample.id,
                    "sample_no": sample.sample_no,
                    "barcode_value": sample.barcode_value,
                    "qr_value": sample.qr_value,
                    "lab_order_id": sample.lab_order_id,
                    "lab_order_no": order.lab_order_no,
                    "patient_id": sample.patient_id,
                    "sample_type": sample.sample_type,
                    "container_type": sample.container_type,
                    "status": sample.status,
                    "collected_by": sample.collected_by,
                    "collected_at": sample.collected_at,
                    "collection_site": sample.collection_site,
                    "collector_notes": sample.collector_notes,
                    "received_in_lab_at": sample.received_in_lab_at,
                    "received_location": sample.received_location,
                    "received_by": sample.received_by,
                    "rejected_at": sample.rejected_at,
                    "rejected_by": sample.rejected_by,
                    "rejection_reason": sample.rejection_reason,
                    "rejection_notes": sample.rejection_notes,
                    "volume_ml": sample.volume_ml,
                    "notes": sample.notes,
                    "tests": tests,
                    "created_at": sample.created_at,
                    "updated_at": sample.updated_at
                })
            
            return sample_responses
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch samples: {str(e)}"
                }
            )
    
    async def get_samples(
        self,
        page: int = 1,
        limit: int = 50,
        status_filter: Optional[SampleStatus] = None,
        sample_type_filter: Optional[SampleType] = None,
        patient_id_filter: Optional[str] = None,
        order_no_filter: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get paginated list of samples with filtering.
        
        Args:
            page: Page number
            limit: Items per page
            status_filter: Filter by sample status
            sample_type_filter: Filter by sample type
            patient_id_filter: Filter by patient ID
            order_no_filter: Filter by order number
            date_from: Filter from date
            date_to: Filter to date
            
        Returns:
            Dictionary with samples and pagination info
        """
        try:
            if date_from is not None:
                date_from = ensure_datetime_utc_aware(date_from)
            if date_to is not None:
                date_to = ensure_datetime_utc_aware(date_to)

            # Build query conditions
            conditions = [Sample.hospital_id == self.hospital_id]
            
            if status_filter:
                conditions.append(Sample.status == status_filter.value)
            
            if sample_type_filter:
                conditions.append(Sample.sample_type == sample_type_filter.value)
            
            if patient_id_filter:
                conditions.append(Sample.patient_id.ilike(f"%{patient_id_filter}%"))
            
            if order_no_filter:
                # Join with LabOrder to filter by order number
                order_subquery = select(LabOrder.id).where(
                    LabOrder.lab_order_no.ilike(f"%{order_no_filter}%")
                ).scalar_subquery()
                conditions.append(Sample.lab_order_id.in_(order_subquery))
            
            if date_from:
                conditions.append(Sample.created_at >= date_from)
            
            if date_to:
                conditions.append(Sample.created_at <= date_to)
            
            # Get total count
            count_query = select(func.count(Sample.id)).where(and_(*conditions))
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Calculate pagination
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit
            
            # Get samples with order info
            samples_query = (
                select(Sample, LabOrder)
                .join(LabOrder, Sample.lab_order_id == LabOrder.id)
                .where(and_(*conditions))
                .order_by(desc(Sample.created_at))
                .offset(offset)
                .limit(limit)
            )
            
            samples_result = await self.db.execute(samples_query)
            samples_data = samples_result.all()
            
            sample_responses = []
            for sample, order in samples_data:
                # Get associated tests for each sample
                tests_result = await self.db.execute(
                    select(SampleOrderItem, LabOrderItem, LabTest)
                    .join(LabOrderItem, SampleOrderItem.lab_order_item_id == LabOrderItem.id)
                    .join(LabTest, LabOrderItem.test_id == LabTest.id)
                    .where(SampleOrderItem.sample_id == sample.id)
                )
                
                tests_data = tests_result.all()
                tests = []
                for _, item, test in tests_data:
                    tests.append({
                        "order_item_id": item.id,
                        "test_id": test.id,
                        "test_code": test.test_code,
                        "test_name": test.test_name,
                        "test_status": item.status
                    })
                
                sample_responses.append({
                    "sample_id": sample.id,
                    "sample_no": sample.sample_no,
                    "barcode_value": sample.barcode_value,
                    "qr_value": sample.qr_value,
                    "lab_order_id": sample.lab_order_id,
                    "lab_order_no": order.lab_order_no,
                    "patient_id": sample.patient_id,
                    "sample_type": sample.sample_type,
                    "container_type": sample.container_type,
                    "status": sample.status,
                    "collected_by": sample.collected_by,
                    "collected_at": sample.collected_at,
                    "collection_site": sample.collection_site,
                    "collector_notes": sample.collector_notes,
                    "received_in_lab_at": sample.received_in_lab_at,
                    "received_location": sample.received_location,
                    "received_by": sample.received_by,
                    "rejected_at": sample.rejected_at,
                    "rejected_by": sample.rejected_by,
                    "rejection_reason": sample.rejection_reason,
                    "rejection_notes": sample.rejection_notes,
                    "volume_ml": sample.volume_ml,
                    "notes": sample.notes,
                    "tests": tests,
                    "created_at": sample.created_at,
                    "updated_at": sample.updated_at
                })
            
            return {
                "samples": sample_responses,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch samples: {str(e)}"
                }
            )
    
    async def get_sample_by_id(self, sample_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get sample details by ID.
        
        Args:
            sample_id: UUID of the sample
            
        Returns:
            Dictionary with sample details
        """
        try:
            # Get sample with order info
            sample_result = await self.db.execute(
                select(Sample, LabOrder)
                .join(LabOrder, Sample.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        Sample.id == sample_id,
                        Sample.hospital_id == self.hospital_id
                    )
                )
            )
            
            sample_data = sample_result.first()
            if not sample_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "SAMPLE_NOT_FOUND",
                        "message": f"Sample with ID {sample_id} not found"
                    }
                )
            
            sample, order = sample_data
            
            # Get associated tests
            tests_result = await self.db.execute(
                select(SampleOrderItem, LabOrderItem, LabTest)
                .join(LabOrderItem, SampleOrderItem.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(SampleOrderItem.sample_id == sample.id)
            )
            
            tests_data = tests_result.all()
            tests = []
            for _, item, test in tests_data:
                tests.append({
                    "order_item_id": item.id,
                    "test_id": test.id,
                    "test_code": test.test_code,
                    "test_name": test.test_name,
                    "test_status": item.status
                })
            
            return {
                "sample_id": sample.id,
                "sample_no": sample.sample_no,
                "barcode_value": sample.barcode_value,
                "qr_value": sample.qr_value,
                "lab_order_id": sample.lab_order_id,
                "lab_order_no": order.lab_order_no,
                "patient_id": sample.patient_id,
                "sample_type": sample.sample_type,
                "container_type": sample.container_type,
                "status": sample.status,
                "collected_by": sample.collected_by,
                "collected_at": sample.collected_at,
                "collection_site": sample.collection_site,
                "collector_notes": sample.collector_notes,
                "received_in_lab_at": sample.received_in_lab_at,
                "received_location": sample.received_location,
                "received_by": sample.received_by,
                "rejected_at": sample.rejected_at,
                "rejected_by": sample.rejected_by,
                "rejection_reason": sample.rejection_reason,
                "rejection_notes": sample.rejection_notes,
                "volume_ml": sample.volume_ml,
                "notes": sample.notes,
                "tests": tests,
                "created_at": sample.created_at,
                "updated_at": sample.updated_at
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch sample: {str(e)}"
                }
            )
    
    async def get_sample_barcode(self, sample_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get barcode information for a sample.
        
        Args:
            sample_id: UUID of the sample
            
        Returns:
            Dictionary with barcode metadata
        """
        try:
            sample_result = await self.db.execute(
                select(Sample, LabOrder)
                .join(LabOrder, Sample.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        Sample.id == sample_id,
                        Sample.hospital_id == self.hospital_id
                    )
                )
            )
            
            sample_data = sample_result.first()
            if not sample_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "SAMPLE_NOT_FOUND",
                        "message": f"Sample with ID {sample_id} not found"
                    }
                )
            
            sample, order = sample_data
            
            barcode_value = sample.barcode_value or sample.sample_no
            barcode_url = f"/api/v1/lab/samples/{sample.id}/barcode.png"
            
            return {
                "sample_id": sample.id,
                "sample_no": sample.sample_no,
                "barcode_value": barcode_value,
                "barcode": barcode_value,
                "barcode_url": barcode_url,
                "qr_value": sample.qr_value,
                "sample_type": sample.sample_type,
                "patient_id": sample.patient_id,
                "lab_order_no": order.lab_order_no,
                "status": sample.status,
                "barcode_format": "CODE128",
                "display_text": sample.sample_no
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch barcode: {str(e)}"
                }
            )
    
    async def scan_sample_by_barcode(self, barcode_value: str) -> Dict[str, Any]:
        """
        Find sample by barcode value.
        
        Args:
            barcode_value: Barcode to scan
            
        Returns:
            Dictionary with sample details
        """
        try:
            sample_result = await self.db.execute(
                select(Sample, LabOrder)
                .join(LabOrder, Sample.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        Sample.barcode_value == barcode_value,
                        Sample.hospital_id == self.hospital_id
                    )
                )
            )
            
            sample_data = sample_result.first()
            if not sample_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "BARCODE_NOT_FOUND",
                        "message": f"No sample found with barcode '{barcode_value}'"
                    }
                )
            
            sample, order = sample_data
            
            # Get associated tests
            tests_result = await self.db.execute(
                select(SampleOrderItem, LabOrderItem, LabTest)
                .join(LabOrderItem, SampleOrderItem.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(SampleOrderItem.sample_id == sample.id)
            )
            
            tests_data = tests_result.all()
            tests = []
            for _, item, test in tests_data:
                tests.append({
                    "order_item_id": item.id,
                    "test_id": test.id,
                    "test_code": test.test_code,
                    "test_name": test.test_name,
                    "test_status": item.status
                })
            
            return {
                "sample_id": sample.id,
                "sample_no": sample.sample_no,
                "barcode_value": sample.barcode_value,
                "qr_value": sample.qr_value,
                "lab_order_id": sample.lab_order_id,
                "lab_order_no": order.lab_order_no,
                "patient_id": sample.patient_id,
                "sample_type": sample.sample_type,
                "container_type": sample.container_type,
                "status": sample.status,
                "collected_by": sample.collected_by,
                "collected_at": sample.collected_at,
                "collection_site": sample.collection_site,
                "collector_notes": sample.collector_notes,
                "received_in_lab_at": sample.received_in_lab_at,
                "received_location": sample.received_location,
                "received_by": sample.received_by,
                "rejected_at": sample.rejected_at,
                "rejected_by": sample.rejected_by,
                "rejection_reason": sample.rejection_reason,
                "rejection_notes": sample.rejection_notes,
                "volume_ml": sample.volume_ml,
                "notes": sample.notes,
                "tests": tests,
                "created_at": sample.created_at,
                "updated_at": sample.updated_at
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "SCAN_FAILED",
                    "message": f"Failed to scan barcode: {str(e)}"
                }
            )
    
    async def collect_sample(
        self, 
        sample_id: uuid.UUID, 
        collect_data: Dict[str, Any],
        collected_by: str
    ) -> Dict[str, Any]:
        """
        Mark sample as collected.
        
        Args:
            sample_id: UUID of the sample
            collect_data: Collection details
            collected_by: User ID who collected
            
        Returns:
            Dictionary with collection result
        """
        try:
            # Get sample and verify status
            sample_result = await self.db.execute(
                select(Sample).where(
                    and_(
                        Sample.id == sample_id,
                        Sample.hospital_id == self.hospital_id
                    )
                )
            )
            
            sample = sample_result.scalar_one_or_none()
            if not sample:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "SAMPLE_NOT_FOUND",
                        "message": f"Sample with ID {sample_id} not found"
                    }
                )
            
            # Validate status transition
            if sample.status != SampleStatus.REGISTERED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_STATUS_TRANSITION",
                        "message": f"Cannot collect sample with status {sample.status}. Must be REGISTERED."
                    }
                )
            
            # Update sample
            await self.db.execute(
                update(Sample)
                .where(Sample.id == sample_id)
                .values(
                    status=SampleStatus.COLLECTED,
                    collected_by=collected_by,
                    collected_at=datetime.utcnow(),
                    collection_site=collect_data.get('collection_site'),
                    collector_notes=collect_data.get('collector_notes'),
                    volume_ml=collect_data.get('volume_ml')
                )
            )
            
            await self.db.commit()
            
            return {
                "message": "Sample collected successfully",
                "sample_id": str(sample_id),
                "sample_no": sample.sample_no,
                "status": SampleStatus.COLLECTED,
                "collected_by": collected_by,
                "collected_at": datetime.utcnow().isoformat()
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "COLLECTION_FAILED",
                    "message": f"Failed to collect sample: {str(e)}"
                }
            )
    
    async def receive_sample(
        self, 
        sample_id: uuid.UUID, 
        receive_data: Dict[str, Any],
        received_by: str
    ) -> Dict[str, Any]:
        """
        Mark sample as received in lab.
        
        Args:
            sample_id: UUID of the sample
            receive_data: Receiving details
            received_by: User ID who received
            
        Returns:
            Dictionary with receiving result
        """
        try:
            # Get sample and verify status
            sample_result = await self.db.execute(
                select(Sample).where(
                    and_(
                        Sample.id == sample_id,
                        Sample.hospital_id == self.hospital_id
                    )
                )
            )
            
            sample = sample_result.scalar_one_or_none()
            if not sample:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "SAMPLE_NOT_FOUND",
                        "message": f"Sample with ID {sample_id} not found"
                    }
                )
            
            # Validate status transition: COLLECTED -> RECEIVED
            if sample.status != SampleStatus.COLLECTED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_STATUS_TRANSITION",
                        "message": f"Cannot receive sample with status {sample.status}. Must be COLLECTED.",
                    },
                )

            # Update sample: RECEIVED (lab has received; use start_analysis to move to IN_PROCESS)
            await self.db.execute(
                update(Sample)
                .where(Sample.id == sample_id)
                .values(
                    status=SampleStatus.RECEIVED,
                    received_by=received_by,
                    received_in_lab_at=datetime.utcnow(),
                    received_location=receive_data.get("received_location"),
                    notes=receive_data.get("notes"),
                )
            )
            await self.db.commit()
            return {
                "message": "Sample received in lab successfully",
                "sample_id": str(sample_id),
                "sample_no": sample.sample_no,
                "status": SampleStatus.RECEIVED,
                "received_by": received_by,
                "received_at": datetime.utcnow().isoformat(),
                "received_location": receive_data.get("received_location"),
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "RECEIVING_FAILED",
                    "message": f"Failed to receive sample: {str(e)}",
                },
            )

    async def start_analysis_sample(self, sample_id: uuid.UUID, started_by: str) -> Dict[str, Any]:
        """Transition sample RECEIVED -> IN_PROCESS (lab starts analysis). Required before result entry."""
        sample_result = await self.db.execute(
            select(Sample).where(
                and_(
                    Sample.id == sample_id,
                    Sample.hospital_id == self.hospital_id,
                )
            )
        )
        sample = sample_result.scalar_one_or_none()
        if not sample:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SAMPLE_NOT_FOUND", "message": f"Sample with ID {sample_id} not found"},
            )
        try:
            current = SampleStatus(sample.status)
        except ValueError:
            current = None
        if current != SampleStatus.RECEIVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_STATUS_TRANSITION",
                    "message": f"Cannot start analysis for sample with status {sample.status}. Must be RECEIVED.",
                },
            )
        await self.db.execute(
            update(Sample).where(Sample.id == sample_id).values(status=SampleStatus.IN_PROCESS)
        )
        await self.db.commit()
        return {
            "message": "Sample in analysis",
            "sample_id": str(sample_id),
            "sample_no": sample.sample_no,
            "status": SampleStatus.IN_PROCESS,
        }

    async def store_sample(self, sample_id: uuid.UUID) -> Dict[str, Any]:
        """Transition sample IN_PROCESS -> STORED (sample stored after use)."""
        sample_result = await self.db.execute(
            select(Sample).where(
                and_(Sample.id == sample_id, Sample.hospital_id == self.hospital_id)
            )
        )
        sample = sample_result.scalar_one_or_none()
        if not sample:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SAMPLE_NOT_FOUND", "message": f"Sample with ID {sample_id} not found"},
            )
        try:
            current = SampleStatus(sample.status)
        except ValueError:
            current = None
        if current != SampleStatus.IN_PROCESS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_STATUS_TRANSITION",
                    "message": f"Cannot store sample with status {sample.status}. Must be IN_PROCESS.",
                },
            )
        await self.db.execute(
            update(Sample).where(Sample.id == sample_id).values(status=SampleStatus.STORED)
        )
        await self.db.commit()
        return {"message": "Sample stored", "sample_id": str(sample_id), "sample_no": sample.sample_no, "status": SampleStatus.STORED}

    async def discard_sample(self, sample_id: uuid.UUID) -> Dict[str, Any]:
        """Transition sample IN_PROCESS -> DISCARDED."""
        sample_result = await self.db.execute(
            select(Sample).where(
                and_(Sample.id == sample_id, Sample.hospital_id == self.hospital_id)
            )
        )
        sample = sample_result.scalar_one_or_none()
        if not sample:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SAMPLE_NOT_FOUND", "message": f"Sample with ID {sample_id} not found"},
            )
        try:
            current = SampleStatus(sample.status)
        except ValueError:
            current = None
        if current != SampleStatus.IN_PROCESS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_STATUS_TRANSITION",
                    "message": f"Cannot discard sample with status {sample.status}. Must be IN_PROCESS.",
                },
            )
        await self.db.execute(
            update(Sample).where(Sample.id == sample_id).values(status=SampleStatus.DISCARDED)
        )
        await self.db.commit()
        return {"message": "Sample discarded", "sample_id": str(sample_id), "sample_no": sample.sample_no, "status": SampleStatus.DISCARDED}

    async def reject_sample(
        self, 
        sample_id: uuid.UUID, 
        reject_data: Dict[str, Any],
        rejected_by: str
    ) -> Dict[str, Any]:
        """
        Reject a sample.
        
        Args:
            sample_id: UUID of the sample
            reject_data: Rejection details
            rejected_by: User ID who rejected
            
        Returns:
            Dictionary with rejection result
        """
        try:
            # Get sample
            sample_result = await self.db.execute(
                select(Sample).where(
                    and_(
                        Sample.id == sample_id,
                        Sample.hospital_id == self.hospital_id
                    )
                )
            )
            
            sample = sample_result.scalar_one_or_none()
            if not sample:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "SAMPLE_NOT_FOUND",
                        "message": f"Sample with ID {sample_id} not found"
                    }
                )
            
            # Validate sample can be rejected
            if sample.status == SampleStatus.REJECTED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "ALREADY_REJECTED",
                        "message": "Sample is already rejected"
                    }
                )
            
            # Update sample
            await self.db.execute(
                update(Sample)
                .where(Sample.id == sample_id)
                .values(
                    status=SampleStatus.REJECTED,
                    rejected_by=rejected_by,
                    rejected_at=datetime.utcnow(),
                    rejection_reason=reject_data['rejection_reason'].value,
                    rejection_notes=reject_data['rejection_notes']
                )
            )
            
            await self.db.commit()
            
            return {
                "message": "Sample rejected successfully",
                "sample_id": str(sample_id),
                "sample_no": sample.sample_no,
                "status": SampleStatus.REJECTED,
                "rejected_by": rejected_by,
                "rejected_at": datetime.utcnow().isoformat(),
                "rejection_reason": reject_data['rejection_reason'].value,
                "rejection_notes": reject_data['rejection_notes']
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "REJECTION_FAILED",
                    "message": f"Failed to reject sample: {str(e)}"
                }
            )
    
    async def bulk_collect_samples(
        self, 
        samples_data: List[Dict[str, Any]],
        collected_by: str
    ) -> Dict[str, Any]:
        """
        Collect multiple samples in bulk.
        
        Args:
            samples_data: List of samples to collect
            collected_by: User ID who collected
            
        Returns:
            Dictionary with bulk collection results
        """
        try:
            collected_samples = []
            failed_samples = []
            
            for sample_data in samples_data:
                try:
                    sample_id = sample_data['sample_id']
                    
                    # Get sample and verify status
                    sample_result = await self.db.execute(
                        select(Sample).where(
                            and_(
                                Sample.id == sample_id,
                                Sample.hospital_id == self.hospital_id
                            )
                        )
                    )
                    
                    sample = sample_result.scalar_one_or_none()
                    if not sample:
                        failed_samples.append({
                            "sample_id": str(sample_id),
                            "error": "Sample not found"
                        })
                        continue
                    
                    # Validate status
                    if sample.status != SampleStatus.REGISTERED:
                        failed_samples.append({
                            "sample_id": str(sample_id),
                            "sample_no": sample.sample_no,
                            "error": f"Invalid status: {sample.status}"
                        })
                        continue
                    
                    # Update sample
                    await self.db.execute(
                        update(Sample)
                        .where(Sample.id == sample_id)
                        .values(
                            status=SampleStatus.COLLECTED,
                            collected_by=collected_by,
                            collected_at=datetime.utcnow(),
                            collection_site=sample_data.get('collection_site'),
                            collector_notes=sample_data.get('collector_notes'),
                            volume_ml=sample_data.get('volume_ml')
                        )
                    )
                    
                    collected_samples.append({
                        "sample_id": str(sample_id),
                        "sample_no": sample.sample_no,
                        "status": SampleStatus.COLLECTED
                    })
                    
                except Exception as e:
                    failed_samples.append({
                        "sample_id": str(sample_data.get('sample_id', 'unknown')),
                        "error": str(e)
                    })
            
            await self.db.commit()
            
            return {
                "collected_samples": collected_samples,
                "failed_samples": failed_samples,
                "total_processed": len(samples_data),
                "successful_count": len(collected_samples),
                "failed_count": len(failed_samples),
                "message": f"Bulk collection completed: {len(collected_samples)} successful, {len(failed_samples)} failed"
            }
            
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "BULK_COLLECTION_FAILED",
                    "message": f"Failed to perform bulk collection: {str(e)}"
                }
            )
    
    # ============================================================================
    # UTILITY METHODS FOR SAMPLES
    # ============================================================================
    
    async def _generate_sample_number(self) -> str:
        """
        Generate unique sample number in format SMP-YYYY-NNNNN.
        
        Returns:
            Unique sample number
        """
        current_year = datetime.utcnow().year
        
        # Get the latest sample number for this year and hospital
        latest_result = await self.db.execute(
            select(Sample.sample_no)
            .where(
                and_(
                    Sample.hospital_id == self.hospital_id,
                    Sample.sample_no.like(f"SMP-{current_year}-%")
                )
            )
            .order_by(desc(Sample.sample_no))
            .limit(1)
        )
        
        latest_sample_no = latest_result.scalar_one_or_none()
        
        if latest_sample_no:
            # Extract the sequence number and increment
            sequence_part = latest_sample_no.split('-')[-1]
            next_sequence = int(sequence_part) + 1
        else:
            # First sample of the year
            next_sequence = 1
        
        # Format with zero padding
        return f"SMP-{current_year}-{next_sequence:05d}"
    
    async def _get_next_sample_sequence_for_order(self, order_id: uuid.UUID, lab_order_no: str) -> int:
        """
        Get the next sample sequence for barcode generation (LAB-ORD-{order_no}-SMP-N).
        Ensures new samples get unique barcodes when order already has existing samples.
        """
        prefix = f"LAB-ORD-{lab_order_no}-SMP-"
        result = await self.db.execute(
            select(Sample.barcode_value)
            .where(
                and_(
                    Sample.lab_order_id == order_id,
                    Sample.barcode_value.like(f"{prefix}%")
                )
            )
        )
        max_seq = 0
        for (barcode,) in result.all():
            try:
                seq_part = barcode[len(prefix):].strip()
                if seq_part.isdigit():
                    max_seq = max(max_seq, int(seq_part))
            except (ValueError, IndexError):
                continue
        return max_seq + 1
    
    # ============================================================================
    # RESULT ENTRY OPERATIONS
    # ============================================================================

    async def _get_current_result_for_order_item(
        self, order_item_id: uuid.UUID
    ) -> Optional[TestResult]:
        """Get the current (latest by created_at) result for an order item, hospital-scoped."""
        result = await self.db.execute(
            select(TestResult)
            .where(
                and_(
                    TestResult.lab_order_item_id == order_item_id,
                    TestResult.hospital_id == self.hospital_id,
                )
            )
            .order_by(desc(TestResult.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()
    
    async def create_result_for_order(
        self,
        order_id: uuid.UUID,
        test_id: uuid.UUID,
        result_data: Dict[str, Any],
        entered_by: str
    ) -> Dict[str, Any]:
        """
        Create or update test result using order_id and test_id.
        Resolves to order_item_id and delegates to create_result.
        """
        item_result = await self.db.execute(
            select(LabOrderItem)
            .join(LabOrder, LabOrderItem.lab_order_id == LabOrder.id)
            .where(
                and_(
                    LabOrderItem.lab_order_id == order_id,
                    LabOrderItem.test_id == test_id,
                    LabOrder.hospital_id == self.hospital_id,
                )
            )
        )
        order_item = item_result.scalar_one_or_none()
        if not order_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "ORDER_ITEM_NOT_FOUND",
                    "message": f"No order item found for order {order_id} and test {test_id}"
                }
            )
        return await self.create_result(order_item.id, result_data, entered_by)

    async def create_result(
        self, 
        order_item_id: uuid.UUID, 
        result_data: Dict[str, Any],
        entered_by: str
    ) -> Dict[str, Any]:
        """
        Create or update test result for an order item.
        
        Args:
            order_item_id: UUID of the lab order item
            result_data: Dictionary containing result information
            entered_by: User ID who is entering results
            
        Returns:
            Dictionary with creation result
            
        Raises:
            HTTPException: If order item not found or sample not in process
        """
        try:
            # Get order item with sample info
            item_result = await self.db.execute(
                select(LabOrderItem, Sample, LabOrder, LabTest)
                .join(SampleOrderItem, LabOrderItem.id == SampleOrderItem.lab_order_item_id)
                .join(Sample, SampleOrderItem.sample_id == Sample.id)
                .join(LabOrder, LabOrderItem.lab_order_id == LabOrder.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(
                    and_(
                        LabOrderItem.id == order_item_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            item_data = item_result.first()
            if not item_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_ITEM_NOT_FOUND",
                        "message": f"Order item with ID {order_item_id} not found"
                    }
                )
            
            order_item, sample, order, test = item_data
            
            # Validate sample is in process
            if sample.status != SampleStatus.IN_PROCESS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "SAMPLE_NOT_IN_PROCESS",
                        "message": f"Cannot enter results for sample with status {sample.status}. Sample must be IN_PROCESS."
                    }
                )
            
            # Current result for this order item (latest by created_at)
            existing = await self._get_current_result_for_order_item(order_item_id)
            
            if existing:
                if existing.status in _RESULT_IMMUTABLE_STATUSES:
                    # Immutable after approval/release: create new version (correction)
                    result = TestResult(
                        hospital_id=self.hospital_id,
                        lab_order_item_id=order_item_id,
                        sample_id=sample.id,
                        status=ResultStatus.DRAFT,
                        entered_by=entered_by,
                        entered_at=datetime.utcnow(),
                        remarks=result_data.get('remarks'),
                        technical_notes=result_data.get('technical_notes'),
                        previous_result_id=existing.id,
                    )
                    self.db.add(result)
                    await self.db.flush()
                    result_id = result.id
                else:
                    # DRAFT or REJECTED: update in place
                    if existing.status not in [ResultStatus.DRAFT, ResultStatus.REJECTED]:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={
                                "code": "RESULT_CANNOT_BE_MODIFIED",
                                "message": f"Cannot modify result with status {existing.status}"
                            }
                        )
                    await self.db.execute(
                        update(TestResult)
                        .where(TestResult.id == existing.id)
                        .values(
                            status=ResultStatus.DRAFT,
                            entered_by=entered_by,
                            entered_at=datetime.utcnow(),
                            remarks=result_data.get('remarks'),
                            technical_notes=result_data.get('technical_notes'),
                            verified_by=None,
                            verified_at=None,
                            verification_notes=None,
                            released_by=None,
                            released_at=None,
                            release_notes=None,
                            rejected_by=None,
                            rejected_at=None,
                            rejection_reason=None,
                        )
                    )
                    await self.db.execute(
                        delete(ResultValue).where(ResultValue.test_result_id == existing.id)
                    )
                    result_id = existing.id
            else:
                # Create new result
                result = TestResult(
                    hospital_id=self.hospital_id,
                    lab_order_item_id=order_item_id,
                    sample_id=sample.id,
                    status=ResultStatus.DRAFT,
                    entered_by=entered_by,
                    entered_at=datetime.utcnow(),
                    remarks=result_data.get('remarks'),
                    technical_notes=result_data.get('technical_notes'),
                )
                self.db.add(result)
                await self.db.flush()
                result_id = result.id
            
            # Reference range from test (JSON: e.g. {"default": "12-16"} or {"HB": "12-16"})
            ref_ranges = (test.reference_ranges or {}) if hasattr(test, "reference_ranges") else {}
            default_range = ref_ranges.get("default") if isinstance(ref_ranges.get("default"), str) else None
            
            # Create result values (validate against normal ranges when reference_range available)
            for value_data in result_data['values']:
                param_name = value_data.get('parameter_name')
                param_range = ref_ranges.get(param_name) if param_name else None
                ref_str = value_data.get('reference_range') or (
                    param_range if isinstance(param_range, str) else default_range
                )
                flag_val = value_data.get('flag')
                is_abnormal_val = value_data.get('is_abnormal')
                if flag_val is None and ref_str:
                    flag_interpreted, is_abnormal_interpreted = _interpret_result_value(
                        value_data['value'], ref_str
                    )
                    flag_val = flag_interpreted.value if flag_interpreted else None
                    is_abnormal_val = is_abnormal_interpreted if is_abnormal_val is None else is_abnormal_val
                if is_abnormal_val is None:
                    is_abnormal_val = False
                result_value = ResultValue(
                    test_result_id=result_id,
                    parameter_name=value_data['parameter_name'],
                    value=value_data['value'],
                    unit=value_data.get('unit'),
                    reference_range=value_data.get('reference_range') or ref_str,
                    flag=flag_val.value if hasattr(flag_val, "value") else flag_val,
                    is_abnormal=bool(is_abnormal_val),
                    display_order=value_data.get('display_order', 1),
                    notes=value_data.get('notes'),
                )
                self.db.add(result_value)
            
            await self.db.commit()
            
            return {
                "result_id": result_id,
                "order_item_id": order_item_id,
                "test_code": test.test_code,
                "test_name": test.test_name,
                "status": ResultStatus.DRAFT,
                "values_count": len(result_data['values']),
                "message": "Test result saved as draft successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "RESULT_CREATION_FAILED",
                    "message": f"Failed to create result: {str(e)}"
                }
            )
    
    async def verify_result(
        self, 
        result_id: uuid.UUID, 
        verify_data: Dict[str, Any],
        verified_by: str
    ) -> Dict[str, Any]:
        """
        Verify a test result (LAB_SUPERVISOR/LAB_ADMIN only).
        
        Args:
            result_id: UUID of the test result
            verify_data: Verification details
            verified_by: User ID who is verifying
            
        Returns:
            Dictionary with verification result
        """
        try:
            # Get result
            result_query = await self.db.execute(
                select(TestResult, LabOrderItem, LabTest)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(
                    and_(
                        TestResult.id == result_id,
                        TestResult.hospital_id == self.hospital_id
                    )
                )
            )
            
            result_data = result_query.first()
            if not result_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "RESULT_NOT_FOUND",
                        "message": f"Result with ID {result_id} not found"
                    }
                )
            
            result, order_item, test = result_data
            
            # Validate status
            if result.status != ResultStatus.DRAFT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_STATUS_TRANSITION",
                        "message": f"Cannot verify result with status {result.status}. Must be DRAFT."
                    }
                )
            
            # Update result
            await self.db.execute(
                update(TestResult)
                .where(TestResult.id == result_id)
                .values(
                    status=ResultStatus.VERIFIED,
                    verified_by=verified_by,
                    verified_at=datetime.utcnow(),
                    verification_notes=verify_data.get('verification_notes')
                )
            )
            
            await self.db.commit()
            
            return {
                "result_id": result_id,
                "test_code": test.test_code,
                "test_name": test.test_name,
                "status": ResultStatus.VERIFIED,
                "verified_by": verified_by,
                "verified_at": datetime.utcnow().isoformat(),
                "message": "Test result verified successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "VERIFICATION_FAILED",
                    "message": f"Failed to verify result: {str(e)}"
                }
            )
    
    async def release_result(
        self, 
        result_id: uuid.UUID, 
        release_data: Dict[str, Any],
        released_by: str
    ) -> Dict[str, Any]:
        """
        Release a verified test result (LAB_SUPERVISOR/LAB_ADMIN only).
        
        Args:
            result_id: UUID of the test result
            release_data: Release details
            released_by: User ID who is releasing
            
        Returns:
            Dictionary with release result
        """
        try:
            # Get result
            result_query = await self.db.execute(
                select(TestResult, LabOrderItem, LabTest)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(
                    and_(
                        TestResult.id == result_id,
                        TestResult.hospital_id == self.hospital_id
                    )
                )
            )
            
            result_data = result_query.first()
            if not result_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "RESULT_NOT_FOUND",
                        "message": f"Result with ID {result_id} not found"
                    }
                )
            
            result, order_item, test = result_data
            
            # Validate status
            if result.status != ResultStatus.VERIFIED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_STATUS_TRANSITION",
                        "message": f"Cannot release result with status {result.status}. Must be VERIFIED."
                    }
                )
            
            # Update result
            await self.db.execute(
                update(TestResult)
                .where(TestResult.id == result_id)
                .values(
                    status=ResultStatus.RELEASED,
                    released_by=released_by,
                    released_at=datetime.utcnow(),
                    release_notes=release_data.get('release_notes')
                )
            )
            
            await self.db.commit()
            
            return {
                "result_id": result_id,
                "test_code": test.test_code,
                "test_name": test.test_name,
                "status": ResultStatus.RELEASED,
                "released_by": released_by,
                "released_at": datetime.utcnow().isoformat(),
                "message": "Test result released successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "RELEASE_FAILED",
                    "message": f"Failed to release result: {str(e)}"
                }
            )
    
    async def reject_result(
        self, 
        result_id: uuid.UUID, 
        reject_data: Dict[str, Any],
        rejected_by: str
    ) -> Dict[str, Any]:
        """
        Reject a test result (LAB_SUPERVISOR/LAB_ADMIN only).
        
        Args:
            result_id: UUID of the test result
            reject_data: Rejection details
            rejected_by: User ID who is rejecting
            
        Returns:
            Dictionary with rejection result
        """
        try:
            # Get result
            result_query = await self.db.execute(
                select(TestResult, LabOrderItem, LabTest)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(
                    and_(
                        TestResult.id == result_id,
                        TestResult.hospital_id == self.hospital_id
                    )
                )
            )
            
            result_data = result_query.first()
            if not result_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "RESULT_NOT_FOUND",
                        "message": f"Result with ID {result_id} not found"
                    }
                )
            
            result, order_item, test = result_data
            
            # Validate status (can reject DRAFT or VERIFIED)
            if result.status not in [ResultStatus.DRAFT, ResultStatus.VERIFIED]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_STATUS_TRANSITION",
                        "message": f"Cannot reject result with status {result.status}. Must be DRAFT or VERIFIED."
                    }
                )
            
            # Update result
            await self.db.execute(
                update(TestResult)
                .where(TestResult.id == result_id)
                .values(
                    status=ResultStatus.REJECTED,
                    rejected_by=rejected_by,
                    rejected_at=datetime.utcnow(),
                    rejection_reason=reject_data['rejection_reason'],
                    # Clear verification/release data if rejecting verified result
                    verified_by=None if result.status == ResultStatus.VERIFIED else result.verified_by,
                    verified_at=None if result.status == ResultStatus.VERIFIED else result.verified_at,
                    verification_notes=None if result.status == ResultStatus.VERIFIED else result.verification_notes
                )
            )
            
            await self.db.commit()
            
            return {
                "result_id": result_id,
                "test_code": test.test_code,
                "test_name": test.test_name,
                "status": ResultStatus.REJECTED,
                "rejected_by": rejected_by,
                "rejected_at": datetime.utcnow().isoformat(),
                "rejection_reason": reject_data['rejection_reason'],
                "message": "Test result rejected successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "REJECTION_FAILED",
                    "message": f"Failed to reject result: {str(e)}"
                }
            )

    async def approve_result(
        self,
        result_id: uuid.UUID,
        approval_data: Dict[str, Any],
        approved_by: str,
    ) -> Dict[str, Any]:
        """
        Approve a test result (pathologist). DRAFT or VERIFIED -> APPROVED.
        After approval the result is immutable; corrections create a new version.
        """
        try:
            result_query = await self.db.execute(
                select(TestResult, LabOrderItem, LabTest)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(
                    and_(
                        TestResult.id == result_id,
                        TestResult.hospital_id == self.hospital_id,
                    )
                )
            )
            row = result_query.first()
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "RESULT_NOT_FOUND", "message": f"Result with ID {result_id} not found"},
                )
            result, order_item, test = row
            if result.status not in (ResultStatus.DRAFT, ResultStatus.VERIFIED):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_STATUS_TRANSITION",
                        "message": f"Cannot approve result with status {result.status}. Must be DRAFT or VERIFIED.",
                    },
                )
            await self.db.execute(
                update(TestResult)
                .where(TestResult.id == result_id)
                .values(
                    status=ResultStatus.APPROVED,
                    approved_by=approved_by,
                    approved_at=datetime.utcnow(),
                    signature_placeholder=approval_data.get("signature_placeholder"),
                )
            )
            await self.db.commit()
            return {
                "result_id": result_id,
                "test_code": test.test_code,
                "test_name": test.test_name,
                "status": ResultStatus.APPROVED,
                "approved_by": approved_by,
                "approved_at": datetime.utcnow().isoformat(),
                "message": "Test result approved successfully",
            }
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "APPROVAL_FAILED", "message": "Failed to approve result"},
            )
    
    async def get_result_by_id(self, result_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get test result details by ID.
        
        Args:
            result_id: UUID of the test result
            
        Returns:
            Dictionary with result details including values
        """
        try:
            # Get result with related data
            result_query = await self.db.execute(
                select(TestResult, LabOrderItem, LabTest, Sample)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .join(Sample, TestResult.sample_id == Sample.id)
                .where(
                    and_(
                        TestResult.id == result_id,
                        TestResult.hospital_id == self.hospital_id
                    )
                )
            )
            
            result_data = result_query.first()
            if not result_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "RESULT_NOT_FOUND",
                        "message": f"Result with ID {result_id} not found"
                    }
                )
            
            result, order_item, test, sample = result_data
            
            # Get result values
            values_query = await self.db.execute(
                select(ResultValue)
                .where(ResultValue.test_result_id == result_id)
                .order_by(ResultValue.display_order)
            )
            
            values = values_query.scalars().all()
            
            # Format values
            values_list = []
            for value in values:
                values_list.append({
                    "value_id": value.id,
                    "parameter_name": value.parameter_name,
                    "value": value.value,
                    "unit": value.unit,
                    "reference_range": value.reference_range,
                    "flag": value.flag,
                    "is_abnormal": value.is_abnormal,
                    "display_order": value.display_order,
                    "notes": value.notes
                })
            
            return {
                "result_id": result.id,
                "lab_order_item_id": result.lab_order_item_id,
                "sample_id": result.sample_id,
                "test_code": test.test_code,
                "test_name": test.test_name,
                "sample_no": sample.sample_no,
                "status": result.status,
                "entered_by": result.entered_by,
                "entered_at": result.entered_at,
                "verified_by": result.verified_by,
                "verified_at": result.verified_at,
                "verification_notes": result.verification_notes,
                "released_by": result.released_by,
                "released_at": result.released_at,
                "release_notes": result.release_notes,
                "rejected_by": result.rejected_by,
                "rejected_at": result.rejected_at,
                "rejection_reason": result.rejection_reason,
                "approved_by": getattr(result, "approved_by", None),
                "approved_at": getattr(result, "approved_at", None),
                "signature_placeholder": getattr(result, "signature_placeholder", None),
                "previous_result_id": getattr(result, "previous_result_id", None),
                "remarks": result.remarks,
                "technical_notes": result.technical_notes,
                "values": values_list,
                "created_at": result.created_at,
                "updated_at": result.updated_at
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch result: {str(e)}"
                }
            )
    
    async def get_worklist(
        self,
        page: int = 1,
        limit: int = 50,
        sample_status_filter: Optional[SampleStatus] = None,
        result_status_filter: Optional[ResultStatus] = None,
        priority_filter: Optional[LabOrderPriority] = None,
        test_code_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get worklist for lab staff showing samples ready for result entry.
        
        Args:
            page: Page number
            limit: Items per page
            sample_status_filter: Filter by sample status
            result_status_filter: Filter by result status
            priority_filter: Filter by order priority
            test_code_filter: Filter by test code
            
        Returns:
            Dictionary with worklist items and summary
        """
        try:
            # Build base query for samples that are IN_PROCESS
            base_conditions = [
                Sample.hospital_id == self.hospital_id,
                Sample.status == SampleStatus.IN_PROCESS
            ]
            
            # Additional filters
            if priority_filter:
                base_conditions.append(LabOrder.priority == priority_filter.value)
            
            if test_code_filter:
                base_conditions.append(LabTest.test_code.ilike(f"%{test_code_filter}%"))
            
            # Build query for worklist items
            worklist_query = (
                select(
                    LabOrderItem.id.label('order_item_id'),
                    LabOrder.id.label('lab_order_id'),
                    LabOrder.lab_order_no,
                    Sample.id.label('sample_id'),
                    Sample.sample_no,
                    Sample.barcode_value,
                    LabOrder.patient_id,
                    LabTest.id.label('test_id'),
                    LabTest.test_code,
                    LabTest.test_name,
                    Sample.sample_type,
                    Sample.status.label('sample_status'),
                    TestResult.status.label('result_status'),
                    LabOrder.priority,
                    Sample.collected_at,
                    Sample.received_in_lab_at,
                    LabTest.turnaround_time_hours,
                    LabOrder.created_at
                )
                .select_from(
                    LabOrderItem
                    .join(LabOrder, LabOrderItem.lab_order_id == LabOrder.id)
                    .join(LabTest, LabOrderItem.test_id == LabTest.id)
                    .join(SampleOrderItem, LabOrderItem.id == SampleOrderItem.lab_order_item_id)
                    .join(Sample, SampleOrderItem.sample_id == Sample.id)
                    .outerjoin(TestResult, LabOrderItem.id == TestResult.lab_order_item_id)
                )
                .where(and_(*base_conditions))
            )
            
            # Apply result status filter
            if result_status_filter:
                worklist_query = worklist_query.where(TestResult.status == result_status_filter.value)
            elif sample_status_filter is None:
                # Default: show items without results or with draft/rejected results
                worklist_query = worklist_query.where(
                    or_(
                        TestResult.status.is_(None),
                        TestResult.status.in_([ResultStatus.DRAFT, ResultStatus.REJECTED])
                    )
                )
            
            # Get total count
            count_query = select(func.count()).select_from(worklist_query.subquery())
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Calculate pagination
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit
            
            # Get paginated results
            paginated_query = (
                worklist_query
                .order_by(
                    # Priority order: URGENT first, then by received time
                    desc(LabOrder.priority == LabOrderPriority.URGENT.value),
                    asc(Sample.received_in_lab_at)
                )
                .offset(offset)
                .limit(limit)
            )
            
            items_result = await self.db.execute(paginated_query)
            items_data = items_result.all()
            
            # Format worklist items
            worklist_items = []
            for item in items_data:
                # Calculate estimated completion
                if item.received_at and item.turnaround_time_hours:
                    estimated_completion = item.received_at + timedelta(hours=item.turnaround_time_hours)
                else:
                    estimated_completion = None
                
                worklist_items.append({
                    "order_item_id": item.order_item_id,
                    "lab_order_id": item.lab_order_id,
                    "lab_order_no": item.lab_order_no,
                    "sample_id": item.sample_id,
                    "sample_no": item.sample_no,
                    "barcode_value": item.barcode_value,
                    "patient_id": item.patient_id,
                    "test_id": item.test_id,
                    "test_code": item.test_code,
                    "test_name": item.test_name,
                    "sample_type": item.sample_type,
                    "sample_status": item.sample_status,
                    "result_status": item.result_status,
                    "priority": item.priority,
                    "collected_at": item.collected_at,
                    "received_at": item.received_at,
                    "turnaround_time_hours": item.turnaround_time_hours,
                    "estimated_completion": estimated_completion
                })
            
            # Get summary statistics
            summary_query = (
                select(
                    func.count().label('total_pending'),
                    func.count().filter(TestResult.status.is_(None)).label('no_results'),
                    func.count().filter(TestResult.status == ResultStatus.DRAFT).label('draft_results'),
                    func.count().filter(TestResult.status == ResultStatus.VERIFIED).label('verified_results'),
                    func.count().filter(TestResult.status == ResultStatus.RELEASED).label('released_results'),
                    func.count().filter(TestResult.status == ResultStatus.REJECTED).label('rejected_results')
                )
                .select_from(
                    LabOrderItem
                    .join(LabOrder, LabOrderItem.lab_order_id == LabOrder.id)
                    .join(SampleOrderItem, LabOrderItem.id == SampleOrderItem.lab_order_item_id)
                    .join(Sample, SampleOrderItem.sample_id == Sample.id)
                    .outerjoin(TestResult, LabOrderItem.id == TestResult.lab_order_item_id)
                )
                .where(
                    and_(
                        Sample.hospital_id == self.hospital_id,
                        Sample.status == SampleStatus.IN_PROCESS
                    )
                )
            )
            
            summary_result = await self.db.execute(summary_query)
            summary_data = summary_result.first()
            
            return {
                "items": worklist_items,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1
                },
                "summary": {
                    "total_pending": summary_data.total_pending or 0,
                    "pending_results": summary_data.no_results or 0,
                    "draft_results": summary_data.draft_results or 0,
                    "verified_results": summary_data.verified_results or 0,
                    "released_results": summary_data.released_results or 0,
                    "rejected_results": summary_data.rejected_results or 0
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "WORKLIST_FETCH_FAILED",
                    "message": f"Failed to fetch worklist: {str(e)}"
                }
            )
    
    async def get_results_for_order(self, order_id: uuid.UUID) -> List[Dict[str, Any]]:
        """
        Get all results for a specific lab order.
        
        Args:
            order_id: UUID of the lab order
            
        Returns:
            List of result details
        """
        try:
            # Verify order exists
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Get all result versions for order items; keep current (latest by created_at) per item
            results_query = await self.db.execute(
                select(TestResult, LabOrderItem, LabTest, Sample)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .join(Sample, TestResult.sample_id == Sample.id)
                .where(LabOrderItem.lab_order_id == order_id)
                .order_by(desc(TestResult.created_at))
            )
            results_data = results_query.all()
            # One row per (result, order_item, test, sample); pick latest result per order_item_id
            current_by_item: Dict[uuid.UUID, tuple] = {}
            for result, order_item, test, sample in results_data:
                if result.lab_order_item_id not in current_by_item:
                    current_by_item[result.lab_order_item_id] = (result, order_item, test, sample)
            
            results_list = []
            for result, order_item, test, sample in current_by_item.values():
                # Get result values
                values_query = await self.db.execute(
                    select(ResultValue)
                    .where(ResultValue.test_result_id == result.id)
                    .order_by(ResultValue.display_order)
                )
                
                values = values_query.scalars().all()
                
                values_list = []
                for value in values:
                    values_list.append({
                        "value_id": value.id,
                        "parameter_name": value.parameter_name,
                        "value": value.value,
                        "unit": value.unit,
                        "reference_range": value.reference_range,
                        "flag": value.flag,
                        "is_abnormal": value.is_abnormal,
                        "display_order": value.display_order,
                        "notes": value.notes
                    })
                
                results_list.append({
                    "result_id": result.id,
                    "lab_order_item_id": result.lab_order_item_id,
                    "sample_id": result.sample_id,
                    "test_code": test.test_code,
                    "test_name": test.test_name,
                    "sample_no": sample.sample_no,
                    "status": result.status,
                    "entered_by": result.entered_by,
                    "entered_at": result.entered_at,
                    "verified_by": result.verified_by,
                    "verified_at": result.verified_at,
                    "verification_notes": result.verification_notes,
                    "released_by": result.released_by,
                    "released_at": result.released_at,
                    "release_notes": result.release_notes,
                    "rejected_by": result.rejected_by,
                    "rejected_at": result.rejected_at,
                    "rejection_reason": result.rejection_reason,
                    "remarks": result.remarks,
                    "technical_notes": result.technical_notes,
                    "values": values_list,
                    "created_at": result.created_at,
                    "updated_at": result.updated_at
                })
            
            return results_list
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch results: {str(e)}"
                }
            )
    
    # ============================================================================
    # REPORT GENERATION OPERATIONS
    # ============================================================================
    
    async def generate_report(
        self, 
        order_id: uuid.UUID, 
        report_data: Dict[str, Any],
        generated_by: str
    ) -> Dict[str, Any]:
        """
        Generate PDF report for a lab order.
        
        Args:
            order_id: UUID of the lab order
            report_data: Report generation options
            generated_by: User ID who is generating report
            
        Returns:
            Dictionary with report details
        """
        try:
            # Get order with results
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Get all result versions for the order; keep current (latest by created_at) per order item
            results_query = await self.db.execute(
                select(TestResult, LabOrderItem, LabTest)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .join(LabTest, LabOrderItem.test_id == LabTest.id)
                .where(LabOrderItem.lab_order_id == order_id)
                .order_by(desc(TestResult.created_at))
            )
            results_data = results_query.all()
            current_by_item: Dict[uuid.UUID, tuple] = {}
            for result, order_item, test in results_data:
                if result.lab_order_item_id not in current_by_item:
                    current_by_item[result.lab_order_item_id] = (result, order_item, test)
            current_results = list(current_by_item.values())

            # Filter: include_draft allows DRAFT/REJECTED; otherwise only APPROVED or RELEASED
            include_draft = report_data.get('include_draft', False)
            valid_statuses = {ResultStatus.RELEASED, ResultStatus.APPROVED}
            if include_draft:
                valid_statuses |= {ResultStatus.DRAFT, ResultStatus.REJECTED, ResultStatus.VERIFIED}
            valid_results = [
                (result, order_item, test)
                for result, order_item, test in current_results
                if result.status in valid_statuses
            ]

            if not valid_results:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "NO_RESULTS_AVAILABLE",
                        "message": "No approved/released results available for report generation"
                    }
                )
            
            # Generate report number
            report_number = await self._generate_report_number()
            
            # Check if report already exists for this order (hospital-scoped)
            existing_report = await self.db.execute(
                select(LabReport).where(
                    and_(
                        LabReport.hospital_id == self.hospital_id,
                        LabReport.lab_order_id == order_id,
                        LabReport.is_active == True
                    )
                )
            )
            
            existing = existing_report.scalar_one_or_none()
            report_version = (existing.report_version + 1) if existing else 1
            
            # Deactivate existing report if regenerating
            if existing:
                await self.db.execute(
                    update(LabReport)
                    .where(LabReport.id == existing.id)
                    .values(is_active=False)
                )
            
            # Build structured payload for PDF regeneration (tests with result values)
            payload_tests = []
            for result, order_item, test in valid_results:
                values_query = await self.db.execute(
                    select(ResultValue)
                    .where(ResultValue.test_result_id == result.id)
                    .order_by(ResultValue.display_order)
                )
                values = values_query.scalars().all()
                payload_tests.append({
                    "test_code": test.test_code,
                    "test_name": test.test_name,
                    "status": result.status,
                    "values": [
                        {
                            "parameter_name": v.parameter_name,
                            "value": v.value,
                            "unit": v.unit,
                            "reference_range": v.reference_range,
                            "flag": v.flag,
                            "is_abnormal": v.is_abnormal,
                        }
                        for v in values
                    ],
                })
            generated_at_iso = datetime.utcnow().isoformat()
            report_payload = {
                "order_no": order.lab_order_no,
                "patient_id": order.patient_id,
                "generated_at": generated_at_iso,
                "include_draft": include_draft,
                "report_notes": report_data.get("report_notes"),
                "tests": payload_tests,
            }

            # Create report record
            report = LabReport(
                hospital_id=self.hospital_id,
                lab_order_id=order_id,
                report_number=report_number,
                report_version=report_version,
                generated_by=generated_by,
                generated_at=datetime.utcnow(),
                total_tests=len(current_results),
                completed_tests=len(valid_results),
                is_final=not include_draft,
                is_active=True,
                report_data=report_payload,
            )
            
            self.db.add(report)
            await self.db.commit()
            await self.db.refresh(report)

            # TODO: Implement actual PDF generation here
            # For now, we'll just create the database record
            
            return {
                "report_id": report.id,
                "report_number": report.report_number,
                "report_version": report.report_version,
                "lab_order_id": order_id,
                "lab_order_no": order.lab_order_no,
                "patient_id": order.patient_id,
                "generated_by": generated_by,
                "generated_at": report.generated_at,
                "total_tests": report.total_tests,
                "completed_tests": report.completed_tests,
                "is_final": report.is_final,
                "message": f"Report {report.report_number} generated successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "REPORT_GENERATION_FAILED",
                    "message": f"Failed to generate report: {str(e)}"
                }
            )
    
    async def get_report_history(self, order_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get report generation history for an order.
        
        Args:
            order_id: UUID of the lab order
            
        Returns:
            Dictionary with report history
        """
        try:
            # Verify order exists
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Get all reports for this order
            reports_query = await self.db.execute(
                select(LabReport)
                .where(LabReport.lab_order_id == order_id)
                .order_by(desc(LabReport.report_version))
            )
            
            reports = reports_query.scalars().all()
            
            reports_list = []
            for report in reports:
                reports_list.append({
                    "report_id": report.id,
                    "report_number": report.report_number,
                    "report_version": report.report_version,
                    "lab_order_id": report.lab_order_id,
                    "lab_order_no": order.lab_order_no,
                    "patient_id": order.patient_id,
                    "pdf_path": report.pdf_path,
                    "pdf_blob_ref": report.pdf_blob_ref,
                    "generated_by": report.generated_by,
                    "generated_at": report.generated_at,
                    "total_tests": report.total_tests,
                    "completed_tests": report.completed_tests,
                    "is_final": report.is_final,
                    "is_active": report.is_active
                })
            
            return {
                "reports": reports_list,
                "total_versions": len(reports_list)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch report history: {str(e)}"
                }
            )
    
    async def _generate_report_number(self) -> str:
        """
        Generate unique report number in format RPT-YYYY-NNNNN.
        
        Returns:
            Unique report number
        """
        current_year = datetime.utcnow().year
        
        # Get the latest report number for this year and hospital
        latest_result = await self.db.execute(
            select(LabReport.report_number)
            .where(
                and_(
                    LabReport.hospital_id == self.hospital_id,
                    LabReport.report_number.like(f"RPT-{current_year}-%")
                )
            )
            .order_by(desc(LabReport.report_number))
            .limit(1)
        )
        
        latest_report_no = latest_result.scalar_one_or_none()
        
        if latest_report_no:
            # Extract the sequence number and increment
            sequence_part = latest_report_no.split('-')[-1]
            next_sequence = int(sequence_part) + 1
        else:
            # First report of the year
            next_sequence = 1
        
        # Format with zero padding
        return f"RPT-{current_year}-{next_sequence:05d}"
    # ============================================================================
    # EQUIPMENT MANAGEMENT OPERATIONS
    # ============================================================================
    
    async def create_equipment(self, equipment_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new lab equipment.
        
        Args:
            equipment_data: Dictionary containing equipment information
            
        Returns:
            Dictionary with creation result and equipment details
            
        Raises:
            HTTPException: If equipment code already exists or validation fails
        """
        try:
            # Check if equipment code already exists in this hospital
            existing_equipment = await self.db.execute(
                select(Equipment).where(
                    and_(
                        Equipment.hospital_id == self.hospital_id,
                        Equipment.equipment_code == equipment_data['equipment_code'].upper()
                    )
                )
            )
            
            if existing_equipment.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "DUPLICATE_EQUIPMENT_CODE",
                        "message": f"Equipment code '{equipment_data['equipment_code']}' already exists in this hospital"
                    }
                )
            
            # Create new equipment
            equipment = Equipment(
                hospital_id=self.hospital_id,
                equipment_code=equipment_data['equipment_code'].upper(),
                name=equipment_data['name'],
                category=equipment_data['category'],
                manufacturer=equipment_data.get('manufacturer'),
                model=equipment_data.get('model'),
                serial_number=equipment_data.get('serial_number'),
                location=equipment_data.get('location'),
                installation_date=equipment_data.get('installation_date'),
                next_calibration_due_at=equipment_data.get('next_calibration_due_at'),
                notes=equipment_data.get('notes'),
                specifications=equipment_data.get('specifications'),
                status="ACTIVE",
                is_active=True
            )
            
            self.db.add(equipment)
            await self.db.commit()
            await self.db.refresh(equipment)
            
            return {
                "equipment_id": equipment.id,
                "equipment_code": equipment.equipment_code,
                "name": equipment.name,
                "category": equipment.category,
                "status": equipment.status,
                "message": "Equipment created successfully"
            }
            
        except HTTPException:
            raise
        except IntegrityError as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "DATABASE_ERROR",
                    "message": "Failed to create equipment due to database constraint"
                }
            )
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "CREATION_FAILED",
                    "message": f"Failed to create equipment: {str(e)}"
                }
            )
    
    async def get_equipment_list(
        self, 
        page: int = 1, 
        limit: int = 50,
        category_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        active_only: bool = True
    ) -> Dict[str, Any]:
        """
        Get paginated list of equipment with filtering options.
        
        Args:
            page: Page number (1-based)
            limit: Items per page
            category_filter: Filter by equipment category
            status_filter: Filter by equipment status
            active_only: Filter only active equipment
            
        Returns:
            Dictionary with equipment and pagination info
        """
        try:
            # Build query conditions
            conditions = [Equipment.hospital_id == self.hospital_id]
            
            if active_only:
                conditions.append(Equipment.is_active == True)
            
            if category_filter:
                conditions.append(Equipment.category == category_filter)
            
            if status_filter:
                conditions.append(Equipment.status == status_filter)
            
            # Get total count
            count_query = select(func.count(Equipment.id)).where(and_(*conditions))
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Calculate pagination
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit
            
            # Get equipment
            equipment_query = (
                select(Equipment)
                .where(and_(*conditions))
                .order_by(asc(Equipment.equipment_code))
                .offset(offset)
                .limit(limit)
            )
            
            equipment_result = await self.db.execute(equipment_query)
            equipment_list = equipment_result.scalars().all()
            
            # Convert to response format
            equipment_responses = []
            for equipment in equipment_list:
                equipment_responses.append({
                    "equipment_id": equipment.id,
                    "equipment_code": equipment.equipment_code,
                    "name": equipment.name,
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
                    "updated_at": equipment.updated_at
                })
            
            return {
                "equipment": equipment_responses,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch equipment: {str(e)}"
                }
            )
    
    async def get_equipment_by_id(self, equipment_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get equipment details by ID with hospital isolation.
        
        Args:
            equipment_id: UUID of the equipment
            
        Returns:
            Dictionary with equipment details
            
        Raises:
            HTTPException: If equipment not found
        """
        try:
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(
                        Equipment.id == equipment_id,
                        Equipment.hospital_id == self.hospital_id
                    )
                )
            )
            
            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "EQUIPMENT_NOT_FOUND",
                        "message": f"Equipment with ID {equipment_id} not found"
                    }
                )
            
            return {
                "equipment_id": equipment.id,
                "equipment_code": equipment.equipment_code,
                "name": equipment.name,
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
                "updated_at": equipment.updated_at
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch equipment: {str(e)}"
                }
            )
    
    async def update_equipment(
        self, 
        equipment_id: uuid.UUID, 
        update_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update equipment information with validation.
        
        Args:
            equipment_id: UUID of the equipment to update
            update_data: Dictionary containing fields to update
            
        Returns:
            Dictionary with update result
            
        Raises:
            HTTPException: If equipment not found or validation fails
        """
        try:
            # Get existing equipment
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(
                        Equipment.id == equipment_id,
                        Equipment.hospital_id == self.hospital_id
                    )
                )
            )
            
            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "EQUIPMENT_NOT_FOUND",
                        "message": f"Equipment with ID {equipment_id} not found"
                    }
                )
            
            # Prepare update data
            update_fields = {}
            for field, value in update_data.items():
                if value is not None:
                    update_fields[field] = value
            
            if not update_fields:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "NO_UPDATE_DATA",
                        "message": "No valid update data provided"
                    }
                )
            
            # Update equipment
            await self.db.execute(
                update(Equipment)
                .where(Equipment.id == equipment_id)
                .values(**update_fields)
            )
            
            await self.db.commit()
            
            return {
                "equipment_id": equipment_id,
                "equipment_code": equipment.equipment_code,
                "name": equipment.name,
                "message": "Equipment updated successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "UPDATE_FAILED",
                    "message": f"Failed to update equipment: {str(e)}"
                }
            )
    
    async def update_equipment_status(
        self, 
        equipment_id: uuid.UUID, 
        new_status: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update equipment status.
        
        Args:
            equipment_id: UUID of the equipment
            new_status: New status
            reason: Reason for status change
            
        Returns:
            Dictionary with update result
            
        Raises:
            HTTPException: If equipment not found
        """
        try:
            # Verify equipment exists and belongs to hospital
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(
                        Equipment.id == equipment_id,
                        Equipment.hospital_id == self.hospital_id
                    )
                )
            )
            
            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "EQUIPMENT_NOT_FOUND",
                        "message": f"Equipment with ID {equipment_id} not found"
                    }
                )
            
            # Update status
            await self.db.execute(
                update(Equipment)
                .where(Equipment.id == equipment_id)
                .values(status=new_status)
            )
            
            await self.db.commit()
            
            return {
                "message": f"Equipment status updated to {new_status}",
                "equipment_id": str(equipment_id),
                "equipment_code": equipment.equipment_code,
                "status": new_status,
                "reason": reason
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "STATUS_UPDATE_FAILED",
                    "message": f"Failed to update equipment status: {str(e)}"
                }
            )
    
    # ============================================================================
    # MAINTENANCE LOG OPERATIONS
    # ============================================================================
    
    async def create_maintenance_log(
        self, 
        equipment_id: uuid.UUID, 
        log_data: Dict[str, Any],
        performed_by: str
    ) -> Dict[str, Any]:
        """
        Create a maintenance log entry for equipment.
        
        Args:
            equipment_id: UUID of the equipment
            log_data: Dictionary containing maintenance information
            performed_by: User ID who performed maintenance
            
        Returns:
            Dictionary with creation result
            
        Raises:
            HTTPException: If equipment not found
        """
        try:
            # Verify equipment exists and belongs to hospital
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(
                        Equipment.id == equipment_id,
                        Equipment.hospital_id == self.hospital_id
                    )
                )
            )
            
            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "EQUIPMENT_NOT_FOUND",
                        "message": f"Equipment with ID {equipment_id} not found"
                    }
                )
            
            # Create maintenance log
            maintenance_log = EquipmentMaintenanceLog(
                equipment_id=equipment_id,
                type=log_data['type'],
                performed_by=performed_by,
                performed_at=log_data['performed_at'],
                next_due_at=log_data.get('next_due_at'),
                remarks=log_data.get('remarks'),
                attachment_ref=log_data.get('attachment_ref'),
                cost=log_data.get('cost'),
                service_provider=log_data.get('service_provider'),
                service_ticket_no=log_data.get('service_ticket_no')
            )
            
            self.db.add(maintenance_log)
            
            # Update equipment calibration date if this is a calibration
            if log_data['type'] == 'CALIBRATION':
                await self.db.execute(
                    update(Equipment)
                    .where(Equipment.id == equipment_id)
                    .values(
                        last_calibrated_at=log_data['performed_at'],
                        next_calibration_due_at=log_data.get('next_due_at')
                    )
                )
            
            await self.db.commit()
            await self.db.refresh(maintenance_log)
            
            return {
                "log_id": maintenance_log.id,
                "equipment_id": equipment_id,
                "equipment_code": equipment.equipment_code,
                "type": maintenance_log.type,
                "performed_at": maintenance_log.performed_at,
                "message": "Maintenance log created successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "LOG_CREATION_FAILED",
                    "message": f"Failed to create maintenance log: {str(e)}"
                }
            )
    
    async def get_maintenance_logs(
        self,
        equipment_id: Optional[uuid.UUID] = None,
        page: int = 1,
        limit: int = 50,
        maintenance_type: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get paginated list of maintenance logs with filtering.
        
        Args:
            equipment_id: Filter by specific equipment
            page: Page number
            limit: Items per page
            maintenance_type: Filter by maintenance type
            date_from: Filter from date
            date_to: Filter to date
            
        Returns:
            Dictionary with logs and pagination info
        """
        try:
            if date_from is not None:
                date_from = ensure_datetime_utc_aware(date_from)
            if date_to is not None:
                date_to = ensure_datetime_utc_aware(date_to)

            # Build query conditions
            conditions = []
            
            if equipment_id:
                conditions.append(EquipmentMaintenanceLog.equipment_id == equipment_id)
            else:
                # Join with Equipment to ensure hospital isolation
                conditions.append(Equipment.hospital_id == self.hospital_id)
            
            if maintenance_type:
                conditions.append(EquipmentMaintenanceLog.type == maintenance_type)
            
            if date_from:
                conditions.append(EquipmentMaintenanceLog.performed_at >= date_from)
            
            if date_to:
                conditions.append(EquipmentMaintenanceLog.performed_at <= date_to)
            
            # Build base query
            base_query = (
                select(EquipmentMaintenanceLog, Equipment)
                .join(Equipment, EquipmentMaintenanceLog.equipment_id == Equipment.id)
                .where(and_(*conditions))
            )
            
            # Get total count
            count_query = select(func.count()).select_from(base_query.subquery())
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Calculate pagination
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit
            
            # Get paginated results
            logs_query = (
                base_query
                .order_by(desc(EquipmentMaintenanceLog.performed_at))
                .offset(offset)
                .limit(limit)
            )
            
            logs_result = await self.db.execute(logs_query)
            logs_data = logs_result.all()
            
            # Format response
            logs_list = []
            for log, equipment in logs_data:
                logs_list.append({
                    "log_id": log.id,
                    "equipment_id": log.equipment_id,
                    "equipment_code": equipment.equipment_code,
                    "equipment_name": equipment.name,
                    "type": log.type,
                    "performed_by": log.performed_by,
                    "performed_at": log.performed_at,
                    "next_due_at": log.next_due_at,
                    "remarks": log.remarks,
                    "attachment_ref": log.attachment_ref,
                    "cost": log.cost,
                    "service_provider": log.service_provider,
                    "service_ticket_no": log.service_ticket_no,
                    "created_at": log.created_at,
                    "updated_at": log.updated_at
                })
            
            return {
                "logs": logs_list,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch maintenance logs: {str(e)}"
                }
            )
    
    # ============================================================================
    # QC RULE OPERATIONS
    # ============================================================================
    
    async def create_qc_rule(
        self, 
        rule_data: Dict[str, Any],
        created_by: str
    ) -> Dict[str, Any]:
        """
        Create a QC rule for lab section/test.
        
        Args:
            rule_data: Dictionary containing QC rule information
            created_by: User ID who created the rule
            
        Returns:
            Dictionary with creation result
        """
        try:
            # Create QC rule
            qc_rule = QCRule(
                hospital_id=self.hospital_id,
                section=rule_data['section'],
                test_code=rule_data.get('test_code'),
                frequency=rule_data['frequency'],
                validity_hours=rule_data.get('validity_hours', 24),
                parameter_name=rule_data['parameter_name'],
                min_value=rule_data.get('min_value'),
                max_value=rule_data.get('max_value'),
                target_value=rule_data.get('target_value'),
                description=rule_data.get('description'),
                status="ACTIVE",
                created_by=created_by
            )
            
            self.db.add(qc_rule)
            await self.db.commit()
            await self.db.refresh(qc_rule)
            
            return {
                "rule_id": qc_rule.id,
                "section": qc_rule.section,
                "parameter_name": qc_rule.parameter_name,
                "frequency": qc_rule.frequency,
                "status": qc_rule.status,
                "message": "QC rule created successfully"
            }
            
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "RULE_CREATION_FAILED",
                    "message": f"Failed to create QC rule: {str(e)}"
                }
            )
    
    async def get_qc_rules(
        self,
        page: int = 1,
        limit: int = 50,
        section_filter: Optional[str] = None,
        test_code_filter: Optional[str] = None,
        active_only: bool = True
    ) -> Dict[str, Any]:
        """
        Get paginated list of QC rules with filtering.
        
        Args:
            page: Page number
            limit: Items per page
            section_filter: Filter by lab section
            test_code_filter: Filter by test code
            active_only: Filter only active rules
            
        Returns:
            Dictionary with rules and pagination info
        """
        try:
            # Build query conditions
            conditions = [QCRule.hospital_id == self.hospital_id]
            
            if active_only:
                conditions.append(QCRule.status == "ACTIVE")
            
            if section_filter:
                conditions.append(QCRule.section == section_filter)
            
            if test_code_filter:
                conditions.append(QCRule.test_code == test_code_filter)
            
            # Get total count
            count_query = select(func.count(QCRule.id)).where(and_(*conditions))
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Calculate pagination
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit
            
            # Get rules
            rules_query = (
                select(QCRule)
                .where(and_(*conditions))
                .order_by(asc(QCRule.section), asc(QCRule.parameter_name))
                .offset(offset)
                .limit(limit)
            )
            
            rules_result = await self.db.execute(rules_query)
            rules = rules_result.scalars().all()
            
            # Convert to response format
            rules_list = []
            for rule in rules:
                rules_list.append({
                    "rule_id": rule.id,
                    "section": rule.section,
                    "test_code": rule.test_code,
                    "frequency": rule.frequency,
                    "validity_hours": rule.validity_hours,
                    "parameter_name": rule.parameter_name,
                    "min_value": rule.min_value,
                    "max_value": rule.max_value,
                    "target_value": rule.target_value,
                    "status": rule.status,
                    "description": rule.description,
                    "created_by": rule.created_by,
                    "created_at": rule.created_at,
                    "updated_at": rule.updated_at
                })
            
            return {
                "rules": rules_list,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch QC rules: {str(e)}"
                }
            )
    
    # ============================================================================
    # QC RUN OPERATIONS
    # ============================================================================
    
    async def create_qc_run(
        self, 
        run_data: Dict[str, Any],
        run_by: str
    ) -> Dict[str, Any]:
        """
        Create a QC run entry.
        
        Args:
            run_data: Dictionary containing QC run information
            run_by: User ID who performed the QC
            
        Returns:
            Dictionary with creation result
        """
        try:
            # Get QC rule and equipment details
            rule_result = await self.db.execute(
                select(QCRule).where(
                    and_(
                        QCRule.id == run_data['qc_rule_id'],
                        QCRule.hospital_id == self.hospital_id
                    )
                )
            )
            
            rule = rule_result.scalar_one_or_none()
            if not rule:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "QC_RULE_NOT_FOUND",
                        "message": f"QC rule with ID {run_data['qc_rule_id']} not found"
                    }
                )
            
            # Verify equipment exists
            equipment_result = await self.db.execute(
                select(Equipment).where(
                    and_(
                        Equipment.id == run_data['equipment_id'],
                        Equipment.hospital_id == self.hospital_id
                    )
                )
            )
            
            equipment = equipment_result.scalar_one_or_none()
            if not equipment:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "EQUIPMENT_NOT_FOUND",
                        "message": f"Equipment with ID {run_data['equipment_id']} not found"
                    }
                )
            
            # Calculate validity expiration
            run_at = run_data['run_at']
            if isinstance(run_at, str):
                run_at = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
            valid_until = run_at + timedelta(hours=rule.validity_hours)

            # Validate values against rule min/max; auto-set status and deviation_notes
            values = run_data.get('values')
            validated_status, deviation_notes = _validate_qc_values(rule, values)
            status_val = run_data.get('status')
            if validated_status is not None:
                status_val = validated_status
            elif status_val is None:
                status_val = QCStatus.PENDING.value

            # Create QC run
            qc_run = QCRun(
                hospital_id=self.hospital_id,
                equipment_id=run_data['equipment_id'],
                qc_rule_id=run_data['qc_rule_id'],
                section=rule.section,
                run_at=run_at,
                run_by=run_by,
                status=status_val,
                values=run_data.get('values'),
                batch_number=run_data.get('batch_number'),
                lot_number=run_data.get('lot_number'),
                remarks=run_data.get('remarks'),
                valid_until=valid_until,
                deviation_notes=deviation_notes
            )
            
            self.db.add(qc_run)
            await self.db.commit()
            await self.db.refresh(qc_run)
            
            return {
                "run_id": qc_run.id,
                "equipment_id": qc_run.equipment_id,
                "equipment_code": equipment.equipment_code,
                "section": qc_run.section,
                "status": qc_run.status,
                "run_at": qc_run.run_at,
                "valid_until": qc_run.valid_until,
                "message": "QC run created successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "QC_RUN_CREATION_FAILED",
                    "message": f"Failed to create QC run: {str(e)}"
                }
            )
    
    async def get_qc_runs(
        self,
        page: int = 1,
        limit: int = 50,
        section_filter: Optional[str] = None,
        equipment_id_filter: Optional[uuid.UUID] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get paginated list of QC runs with filtering.
        
        Args:
            page: Page number
            limit: Items per page
            section_filter: Filter by lab section
            equipment_id_filter: Filter by equipment
            date_from: Filter from date
            date_to: Filter to date
            
        Returns:
            Dictionary with runs and pagination info
        """
        try:
            if date_from is not None:
                date_from = ensure_datetime_utc_aware(date_from)
            if date_to is not None:
                date_to = ensure_datetime_utc_aware(date_to)

            # Build query conditions
            conditions = [QCRun.hospital_id == self.hospital_id]
            
            if section_filter:
                conditions.append(QCRun.section == section_filter)
            
            if equipment_id_filter:
                conditions.append(QCRun.equipment_id == equipment_id_filter)
            
            if date_from:
                conditions.append(QCRun.run_at >= date_from)
            
            if date_to:
                conditions.append(QCRun.run_at <= date_to)
            
            # Build base query with joins
            base_query = (
                select(QCRun, Equipment, QCRule)
                .join(Equipment, QCRun.equipment_id == Equipment.id)
                .join(QCRule, QCRun.qc_rule_id == QCRule.id)
                .where(and_(*conditions))
            )
            
            # Get total count
            count_query = select(func.count()).select_from(base_query.subquery())
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Calculate pagination
            offset = (page - 1) * limit
            total_pages = (total + limit - 1) // limit
            
            # Get paginated results
            runs_query = (
                base_query
                .order_by(desc(QCRun.run_at))
                .offset(offset)
                .limit(limit)
            )
            
            runs_result = await self.db.execute(runs_query)
            runs_data = runs_result.all()
            
            # Format response
            runs_list = []
            for run, equipment, rule in runs_data:
                runs_list.append({
                    "run_id": run.id,
                    "equipment_id": run.equipment_id,
                    "equipment_code": equipment.equipment_code,
                    "equipment_name": equipment.name,
                    "qc_rule_id": run.qc_rule_id,
                    "section": run.section,
                    "parameter_name": rule.parameter_name,
                    "run_at": run.run_at,
                    "run_by": run.run_by,
                    "status": run.status,
                    "values": run.values,
                    "batch_number": run.batch_number,
                    "lot_number": run.lot_number,
                    "remarks": run.remarks,
                    "valid_until": run.valid_until,
                    "created_at": run.created_at,
                    "updated_at": run.updated_at
                })
            
            return {
                "runs": runs_list,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"Failed to fetch QC runs: {str(e)}"
                }
            )
    
    async def check_qc_status(
        self,
        section: str,
        equipment_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        Check QC status for a lab section or specific equipment.
        
        Args:
            section: Lab section to check
            equipment_id: Optional specific equipment
            
        Returns:
            Dictionary with QC status information
        """
        try:
            # Build query conditions
            conditions = [
                QCRun.hospital_id == self.hospital_id,
                QCRun.section == section,
                QCRun.status == "PASS"
            ]
            
            if equipment_id:
                conditions.append(QCRun.equipment_id == equipment_id)
            
            # Get the latest PASS QC run for the section/equipment
            latest_qc_query = (
                select(QCRun, Equipment)
                .join(Equipment, QCRun.equipment_id == Equipment.id)
                .where(and_(*conditions))
                .order_by(desc(QCRun.run_at))
                .limit(1)
            )
            
            latest_result = await self.db.execute(latest_qc_query)
            latest_data = latest_result.first()
            
            if not latest_data:
                return {
                    "section": section,
                    "equipment_id": equipment_id,
                    "equipment_code": None,
                    "last_qc_pass_time": None,
                    "is_valid": False,
                    "validity_expires_at": None,
                    "hours_remaining": None,
                    "required_frequency": "DAILY",  # Default
                    "next_qc_due": None,
                    "blocking_release": True,
                    "message": "No valid QC found - QC required before result release"
                }
            
            latest_run, equipment = latest_data
            
            # Check if QC is still valid
            current_time = datetime.utcnow()
            is_valid = latest_run.valid_until > current_time
            
            if is_valid:
                time_remaining = latest_run.valid_until - current_time
                hours_remaining = int(time_remaining.total_seconds() / 3600)
            else:
                hours_remaining = 0
            
            # Get QC rule to determine frequency
            rule_result = await self.db.execute(
                select(QCRule).where(QCRule.id == latest_run.qc_rule_id)
            )
            rule = rule_result.scalar_one_or_none()
            frequency = rule.frequency if rule else "DAILY"
            
            return {
                "section": section,
                "equipment_id": equipment_id,
                "equipment_code": equipment.equipment_code,
                "last_qc_pass_time": latest_run.run_at,
                "is_valid": is_valid,
                "validity_expires_at": latest_run.valid_until,
                "hours_remaining": hours_remaining if is_valid else 0,
                "required_frequency": frequency,
                "next_qc_due": latest_run.valid_until,
                "blocking_release": not is_valid,
                "message": f"QC is {'valid' if is_valid else 'expired'} - {'No blocking' if is_valid else 'Blocking result release'}"
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "QC_STATUS_CHECK_FAILED",
                    "message": f"Failed to check QC status: {str(e)}"
                }
            )

    async def create_corrective_action(
        self,
        qc_run_id: uuid.UUID,
        action_taken: str,
        performed_by: str,
        remarks: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record corrective action for a failed QC run.
        """
        try:
            run_result = await self.db.execute(
                select(QCRun).where(
                    and_(
                        QCRun.id == qc_run_id,
                        QCRun.hospital_id == self.hospital_id,
                    )
                )
            )
            qc_run = run_result.scalar_one_or_none()
            if not qc_run:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "QC_RUN_NOT_FOUND", "message": f"QC run {qc_run_id} not found"},
                )
            action = QCCorrectiveAction(
                hospital_id=self.hospital_id,
                qc_run_id=qc_run_id,
                action_taken=action_taken,
                performed_by=performed_by,
                remarks=remarks,
            )
            self.db.add(action)
            await self.db.commit()
            await self.db.refresh(action)
            return {
                "action_id": action.id,
                "qc_run_id": qc_run_id,
                "action_taken": action_taken,
                "performed_at": action.performed_at,
                "message": "Corrective action recorded",
            }
        except HTTPException:
            raise
        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "CORRECTIVE_ACTION_FAILED", "message": str(e)},
            )
    
    # ============================================================================
    # REPORT SHARING & NOTIFICATION OPERATIONS
    # ============================================================================
    
    async def publish_report(
        self, 
        order_id: uuid.UUID, 
        publish: bool,
        published_by: uuid.UUID,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Publish or unpublish a lab report for external access.
        
        Args:
            order_id: UUID of the lab order
            publish: True to publish, False to unpublish
            published_by: User ID performing the action
            reason: Reason for publish/unpublish
            
        Returns:
            Dictionary with publish status
        """
        try:
            # Verify order exists and belongs to hospital
            order_result = await self.db.execute(
                select(LabOrder).where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == self.hospital_id
                    )
                )
            )
            
            order = order_result.scalar_one_or_none()
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "ORDER_NOT_FOUND",
                        "message": f"Order with ID {order_id} not found"
                    }
                )
            
            # Get the latest report for this order
            report_result = await self.db.execute(
                select(LabReport).where(
                    and_(
                        LabReport.lab_order_id == order_id,
                        LabReport.is_active == True
                    )
                ).order_by(desc(LabReport.report_version)).limit(1)
            )
            
            report = report_result.scalar_one_or_none()
            if not report:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "REPORT_NOT_FOUND",
                        "message": f"No report found for order {order.lab_order_no}"
                    }
                )
            
            # Check if all results are released before publishing
            if publish:
                unreleased_results = await self.db.execute(
                    select(func.count(TestResult.id))
                    .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                    .where(
                        and_(
                            LabOrderItem.lab_order_id == order_id,
                            TestResult.status != ResultStatus.RELEASED
                        )
                    )
                )
                
                unreleased_count = unreleased_results.scalar()
                if unreleased_count > 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "RESULTS_NOT_RELEASED",
                            "message": f"Cannot publish report - {unreleased_count} results are not yet released"
                        }
                    )
            
            # Update report publish status (we'll add this field to LabReport model)
            current_time = datetime.utcnow()
            
            # For now, we'll track publish status in the report_data JSON field
            report_data = report.report_data or {}
            if publish:
                report_data.update({
                    "publish_status": "PUBLISHED",
                    "published_at": current_time.isoformat(),
                    "published_by": str(published_by),
                    "publish_reason": reason
                })
            else:
                report_data.update({
                    "publish_status": "UNPUBLISHED",
                    "unpublished_at": current_time.isoformat(),
                    "unpublished_by": str(published_by),
                    "unpublish_reason": reason
                })
            
            await self.db.execute(
                update(LabReport)
                .where(LabReport.id == report.id)
                .values(report_data=report_data)
            )
            
            await self.db.commit()
            
            # Create notification if publishing
            if publish:
                await self._create_report_ready_notification(order, report)
            
            return {
                "lab_order_id": order_id,
                "lab_order_no": order.lab_order_no,
                "report_id": report.id,
                "is_published": publish,
                "published_at": current_time if publish else None,
                "published_by": str(published_by) if publish else None,
                "reason": reason,
                "message": f"Report {'published' if publish else 'unpublished'} successfully"
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "PUBLISH_FAILED",
                    "message": f"Failed to {'publish' if publish else 'unpublish'} report: {str(e)}"
                }
            )
    # ============================================================================
    # REPORT ACCESS & SHARING METHODS (Task 5)
    # ============================================================================
    
    async def get_doctor_reports(
        self,
        doctor_id: str,
        hospital_id: uuid.UUID,
        patient_id: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        page: int = 1,
        limit: int = 10
    ) -> Dict[str, Any]:
        """Get lab reports accessible to a doctor with RBAC enforcement."""
        try:
            # Build query conditions
            conditions = [
                LabOrder.hospital_id == hospital_id,
                LabOrder.requested_by_doctor_id == doctor_id,  # Doctor can only see their orders
                LabReport.publish_status == "PUBLISHED"  # Only published reports
            ]
            
            if patient_id:
                conditions.append(LabOrder.patient_id == patient_id)
            
            if from_date:
                conditions.append(LabReport.generated_at >= from_date)
            
            if to_date:
                conditions.append(LabReport.generated_at <= to_date)
            
            # Get total count
            count_query = (
                select(func.count(LabReport.id))
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(and_(*conditions))
            )
            
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Get paginated results
            offset = (page - 1) * limit
            reports_query = (
                select(LabReport, LabOrder)
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(and_(*conditions))
                .order_by(desc(LabReport.generated_at))
                .offset(offset)
                .limit(limit)
            )
            
            reports_result = await self.db.execute(reports_query)
            reports_data = reports_result.fetchall()
            
            # Format response
            reports = []
            for report, order in reports_data:
                reports.append({
                    "report_id": report.id,
                    "lab_order_no": order.lab_order_no,
                    "patient_id": order.patient_id,
                    "report_date": report.generated_at,
                    "total_tests": report.total_tests,
                    "is_published": True,
                    "encounter_id": order.encounter_id
                })
            
            return {
                "reports": reports,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": (total + limit - 1) // limit
                },
                "summary": {
                    "total_reports": total,
                    "published_reports": total,
                    "pending_reports": 0
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get doctor reports: {str(e)}"
            )
    
    async def get_patient_reports(
        self,
        patient_id: str,
        hospital_id: uuid.UUID,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        page: int = 1,
        limit: int = 10
    ) -> Dict[str, Any]:
        """Get lab reports for a patient (own reports only)."""
        try:
            # Build query conditions
            conditions = [
                LabOrder.hospital_id == hospital_id,
                LabOrder.patient_id == patient_id,  # Patient can only see own reports
                LabReport.publish_status == "PUBLISHED"  # Only published reports
            ]
            
            if from_date:
                conditions.append(LabReport.generated_at >= from_date)
            
            if to_date:
                conditions.append(LabReport.generated_at <= to_date)
            
            # Get total count
            count_query = (
                select(func.count(LabReport.id))
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(and_(*conditions))
            )
            
            total_result = await self.db.execute(count_query)
            total = total_result.scalar()
            
            # Get paginated results
            offset = (page - 1) * limit
            reports_query = (
                select(LabReport, LabOrder)
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(and_(*conditions))
                .order_by(desc(LabReport.generated_at))
                .offset(offset)
                .limit(limit)
            )
            
            reports_result = await self.db.execute(reports_query)
            reports_data = reports_result.fetchall()
            
            # Format response
            reports = []
            for report, order in reports_data:
                # Count abnormal results
                abnormal_count = await self._count_abnormal_results(order.id)
                
                reports.append({
                    "report_id": report.id,
                    "lab_order_no": order.lab_order_no,
                    "report_date": report.generated_at,
                    "total_tests": report.total_tests,
                    "abnormal_count": abnormal_count,
                    "is_final": report.is_final,
                    "requested_by_doctor": order.requested_by_doctor_id
                })
            
            return {
                "reports": reports,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": (total + limit - 1) // limit
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get patient reports: {str(e)}"
            )
    
    async def get_report_with_access_check(
        self,
        report_id: uuid.UUID,
        user_id: uuid.UUID,
        user_role: str,
        hospital_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Get report metadata with RBAC access check."""
        try:
            # Get report with order details
            report_query = (
                select(LabReport, LabOrder)
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        LabReport.id == report_id,
                        LabOrder.hospital_id == hospital_id,
                        LabReport.publish_status == "PUBLISHED"
                    )
                )
            )
            
            report_result = await self.db.execute(report_query)
            report_data = report_result.first()
            
            if not report_data:
                return None
            
            report, order = report_data
            
            # RBAC check
            if user_role == "PATIENT":
                # Patient can only access own reports
                if order.patient_id != str(user_id):
                    return None
            elif user_role == "DOCTOR":
                # Doctor can only access reports for their patients/encounters
                if order.requested_by_doctor_id != str(user_id):
                    return None
            elif user_role not in ["LAB_TECH", "HOSPITAL_ADMIN", "RECEPTIONIST"]:
                # Other roles not allowed (RECEPTIONIST can view any report in hospital)
                return None
            
            # Get test summary
            tests_summary = await self._get_tests_summary(order.id)
            
            return {
                "report_id": report.id,
                "lab_order_id": order.id,
                "lab_order_no": order.lab_order_no,
                "patient_id": order.patient_id,
                "requested_by_doctor_id": order.requested_by_doctor_id,
                "report_number": report.report_number,
                "report_version": report.report_version,
                "generated_at": report.generated_at,
                "total_tests": report.total_tests,
                "completed_tests": report.completed_tests,
                "is_final": report.is_final,
                "is_published": True,
                "pdf_available": bool(report.pdf_path),
                "tests_summary": tests_summary
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get report: {str(e)}"
            )
    
    async def get_report_pdf_with_access_check(
        self,
        report_id: uuid.UUID,
        user_id: uuid.UUID,
        user_role: str,
        hospital_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Get report PDF path with RBAC access check."""
        try:
            # First check access
            report_metadata = await self.get_report_with_access_check(
                report_id, user_id, user_role, hospital_id
            )
            
            if not report_metadata:
                return None
            
            # Get PDF path
            report_query = (
                select(LabReport.pdf_path, LabReport.report_number)
                .where(LabReport.id == report_id)
            )
            
            pdf_result = await self.db.execute(report_query)
            pdf_data = pdf_result.first()
            
            if not pdf_data or not pdf_data[0]:
                return None
            
            return {
                "pdf_path": pdf_data[0],
                "report_number": pdf_data[1]
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get report PDF: {str(e)}"
            )
    
    async def create_share_token(
        self,
        order_id: uuid.UUID,
        viewer_type: str,
        expires_hours: int,
        specific_user_id: Optional[uuid.UUID],
        created_by: uuid.UUID,
        hospital_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Create secure share token for report access."""
        try:
            # Get report for the order
            report_query = (
                select(LabReport, LabOrder)
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == hospital_id,
                        LabReport.is_active == True
                    )
                )
                .order_by(desc(LabReport.report_version))
                .limit(1)
            )
            
            report_result = await self.db.execute(report_query)
            report_data = report_result.first()
            
            if not report_data:
                return None
            
            report, order = report_data
            
            # Generate secure token
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            
            # Calculate expiry
            expires_at = datetime.utcnow() + timedelta(hours=expires_hours)
            
            # Create share token record
            from app.models.lab import ReportShareToken
            
            share_token = ReportShareToken(
                hospital_id=hospital_id,
                lab_order_id=order_id,
                lab_report_id=report.id,
                token=token,
                token_hash=token_hash,
                allowed_viewer_type=viewer_type,
                specific_user_id=specific_user_id,
                expires_at=expires_at,
                is_active=True,
                access_count=0,
                created_by=created_by
            )
            
            self.db.add(share_token)
            await self.db.commit()
            await self.db.refresh(share_token)
            
            # Generate share URL used by the frontend to open shared reports.
            # Keep it configurable (do not hardcode company/domain).
            base_url = str(getattr(settings, "APP_PUBLIC_URL", "") or "").rstrip("/")
            if not base_url:
                base_url = "http://localhost:8060"
            share_url = f"{base_url}/lab/report-share/{token}"
            
            return {
                "share_id": share_token.id,
                "token": token,
                "share_url": share_url,
                "expires_at": expires_at,
                "viewer_type": viewer_type,
                "access_count": 0,
                "is_active": True,
                "created_at": share_token.created_at
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create share token: {str(e)}"
            )
    
    async def validate_share_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Validate share token and return report access info."""
        try:
            from app.models.lab import ReportShareToken
            
            # Get share token
            token_query = (
                select(ReportShareToken, LabReport, LabOrder)
                .join(LabReport, ReportShareToken.lab_report_id == LabReport.id)
                .join(LabOrder, ReportShareToken.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        ReportShareToken.token == token,
                        ReportShareToken.is_active == True,
                        ReportShareToken.expires_at > datetime.utcnow()
                    )
                )
            )
            
            token_result = await self.db.execute(token_query)
            token_data = token_result.first()
            
            if not token_data:
                return None
            
            share_token, report, order = token_data
            
            # Update access count and last accessed
            await self.db.execute(
                update(ReportShareToken)
                .where(ReportShareToken.id == share_token.id)
                .values(
                    access_count=ReportShareToken.access_count + 1,
                    last_accessed_at=datetime.utcnow(),
                    last_accessed_ip=ip_address
                )
            )
            
            # Log access
            await self.log_report_access(
                report_id=report.id,
                accessed_by=None,  # Anonymous access
                access_method="SHARE_TOKEN",
                access_type="VIEW",
                ip_address=ip_address,
                user_agent=user_agent,
                hospital_id=share_token.hospital_id,
                share_token_id=share_token.id
            )
            
            await self.db.commit()
            
            return {
                "report_id": report.id,
                "lab_order_no": order.lab_order_no,
                "patient_id": order.patient_id,
                "report_date": report.generated_at,
                "total_tests": report.total_tests,
                "completed_tests": report.completed_tests,
                "is_final": report.is_final,
                "pdf_available": bool(report.pdf_path),
                "access_method": "SHARE_TOKEN",
                "expires_at": share_token.expires_at
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to validate share token: {str(e)}"
            )
    
    async def log_report_access(
        self,
        report_id: uuid.UUID,
        accessed_by: Optional[uuid.UUID],
        access_method: str,
        access_type: str,
        ip_address: Optional[str],
        user_agent: Optional[str],
        hospital_id: uuid.UUID,
        share_token_id: Optional[uuid.UUID] = None
    ):
        """Log report access for audit trail."""
        try:
            from app.models.lab import ReportAccess
            
            # Get patient_id from report
            patient_query = (
                select(LabOrder.patient_id, LabOrder.id)
                .join(LabReport, LabOrder.id == LabReport.lab_order_id)
                .where(LabReport.id == report_id)
            )
            
            patient_result = await self.db.execute(patient_query)
            patient_data = patient_result.first()
            
            if patient_data:
                access_log = ReportAccess(
                    hospital_id=hospital_id,
                    lab_report_id=report_id,
                    lab_order_id=patient_data[1],
                    accessed_by=accessed_by,
                    access_method=access_method,
                    share_token_id=share_token_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    access_type=access_type,
                    patient_id=patient_data[0]
                )
                
                self.db.add(access_log)
                await self.db.commit()
                
        except Exception as e:
            # Don't fail the main operation if logging fails
            print(f"Failed to log report access: {str(e)}")
    
    async def create_report_ready_notification(
        self,
        order_id: uuid.UUID,
        hospital_id: uuid.UUID
    ):
        """Create notification when report is ready."""
        try:
            from app.models.lab import NotificationOutbox
            
            # Get order details
            order_query = (
                select(LabOrder)
                .where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == hospital_id
                    )
                )
            )
            
            order_result = await self.db.execute(order_query)
            order = order_result.scalar_one_or_none()
            
            if not order:
                return
            
            # Create notifications for patient and doctor
            notifications = []
            
            # Patient notification
            if order.patient_id:
                notifications.append(NotificationOutbox(
                    hospital_id=hospital_id,
                    event_type="LAB_REPORT_READY",
                    event_id=order.lab_order_no,
                    recipient_type="PATIENT",
                    recipient_id=uuid.UUID(order.patient_id),
                    title="Lab Report Ready",
                    message=f"Your lab report for order {order.lab_order_no} is now available.",
                    channel="EMAIL",
                    status="PENDING",
                    retry_count=0,
                    max_retries=3,
                    payload={
                        "lab_order_no": order.lab_order_no,
                        "order_id": str(order_id)
                    }
                ))
            
            # Doctor notification
            if order.requested_by_doctor_id:
                notifications.append(NotificationOutbox(
                    hospital_id=hospital_id,
                    event_type="LAB_REPORT_READY",
                    event_id=order.lab_order_no,
                    recipient_type="DOCTOR",
                    recipient_id=uuid.UUID(order.requested_by_doctor_id),
                    title="Lab Report Ready",
                    message=f"Lab report for patient {order.patient_id} (Order: {order.lab_order_no}) is ready for review.",
                    channel="EMAIL",
                    status="PENDING",
                    retry_count=0,
                    max_retries=3,
                    payload={
                        "lab_order_no": order.lab_order_no,
                        "patient_id": order.patient_id,
                        "order_id": str(order_id)
                    }
                ))
            
            # Add notifications to database
            for notification in notifications:
                self.db.add(notification)
            
            await self.db.commit()
            
        except Exception as e:
            print(f"Failed to create report ready notification: {str(e)}")
    
    # Helper methods
    async def _count_abnormal_results(self, order_id: uuid.UUID) -> int:
        """Count abnormal results in an order."""
        try:
            abnormal_query = (
                select(func.count(ResultValue.id))
                .join(TestResult, ResultValue.test_result_id == TestResult.id)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .where(
                    and_(
                        LabOrderItem.lab_order_id == order_id,
                        ResultValue.is_abnormal == True
                    )
                )
            )
            
            result = await self.db.execute(abnormal_query)
            return result.scalar() or 0
            
        except Exception:
            return 0
    
    async def _get_tests_summary(self, order_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Get summary of tests in an order."""
        try:
            tests_query = (
                select(LabTest.test_code, LabTest.test_name, TestResult.status)
                .join(LabOrderItem, LabTest.id == LabOrderItem.test_id)
                .outerjoin(TestResult, LabOrderItem.id == TestResult.lab_order_item_id)
                .where(LabOrderItem.lab_order_id == order_id)
            )
            
            tests_result = await self.db.execute(tests_query)
            tests_data = tests_result.fetchall()
            
            summary = []
            for test_code, test_name, status in tests_data:
                # Count abnormal results for this test
                abnormal_count = 0  # Simplified for now
                
                summary.append({
                    "test_code": test_code,
                    "test_name": test_name,
                    "status": status or "PENDING",
                    "abnormal_count": abnormal_count
                })
            
            return summary
            
        except Exception:
            return []
    async def publish_report(
        self,
        order_id: uuid.UUID,
        published_by: uuid.UUID,
        hospital_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Publish lab report for external access."""
        try:
            # Get report for the order
            report_query = (
                select(LabReport, LabOrder)
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == hospital_id,
                        LabReport.is_active == True
                    )
                )
                .order_by(desc(LabReport.report_version))
                .limit(1)
            )
            
            report_result = await self.db.execute(report_query)
            report_data = report_result.first()
            
            if not report_data:
                return None
            
            report, order = report_data
            
            # Update publish status
            current_time = datetime.utcnow()
            await self.db.execute(
                update(LabReport)
                .where(LabReport.id == report.id)
                .values(
                    publish_status="PUBLISHED",
                    published_at=current_time,
                    published_by=published_by
                )
            )
            
            await self.db.commit()
            
            return {
                "report_id": report.id,
                "published_at": current_time
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to publish report: {str(e)}"
            )
    
    async def unpublish_report(
        self,
        order_id: uuid.UUID,
        unpublished_by: uuid.UUID,
        hospital_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Unpublish lab report."""
        try:
            # Get report for the order
            report_query = (
                select(LabReport, LabOrder)
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == hospital_id,
                        LabReport.is_active == True
                    )
                )
                .order_by(desc(LabReport.report_version))
                .limit(1)
            )
            
            report_result = await self.db.execute(report_query)
            report_data = report_result.first()
            
            if not report_data:
                return None
            
            report, order = report_data
            
            # Update publish status
            current_time = datetime.utcnow()
            await self.db.execute(
                update(LabReport)
                .where(LabReport.id == report.id)
                .values(
                    publish_status="UNPUBLISHED",
                    unpublished_at=current_time,
                    unpublished_by=unpublished_by
                )
            )
            
            await self.db.commit()
            
            return {
                "report_id": report.id,
                "unpublished_at": current_time
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to unpublish report: {str(e)}"
            )
    
    async def get_report_publish_status(
        self,
        order_id: uuid.UUID,
        hospital_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Get report publish status."""
        try:
            # Get report for the order
            report_query = (
                select(LabReport, LabOrder)
                .join(LabOrder, LabReport.lab_order_id == LabOrder.id)
                .where(
                    and_(
                        LabOrder.id == order_id,
                        LabOrder.hospital_id == hospital_id,
                        LabReport.is_active == True
                    )
                )
                .order_by(desc(LabReport.report_version))
                .limit(1)
            )
            
            report_result = await self.db.execute(report_query)
            report_data = report_result.first()
            
            if not report_data:
                return None
            
            report, order = report_data
            
            return {
                "lab_order_id": order_id,
                "lab_order_no": order.lab_order_no,
                "report_id": report.id,
                "publish_status": report.publish_status,
                "published_at": report.published_at,
                "published_by": report.published_by,
                "unpublished_at": report.unpublished_at,
                "unpublished_by": report.unpublished_by
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get publish status: {str(e)}"
            )
    
    async def revoke_share_token(
        self,
        token: str,
        revoked_by: uuid.UUID,
        hospital_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Revoke share token."""
        try:
            from app.models.lab import ReportShareToken
            
            # Find and revoke token
            current_time = datetime.utcnow()
            result = await self.db.execute(
                update(ReportShareToken)
                .where(
                    and_(
                        ReportShareToken.token == token,
                        ReportShareToken.hospital_id == hospital_id,
                        ReportShareToken.is_active == True
                    )
                )
                .values(
                    is_active=False,
                    revoked_at=current_time,
                    revoked_by=revoked_by
                )
            )
            
            if result.rowcount == 0:
                return None
            
            await self.db.commit()
            
            return {
                "revoked_at": current_time
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to revoke share token: {str(e)}"
            )
    
    async def verify_share_token_otp(
        self,
        token: str,
        otp_code: str
    ) -> bool:
        """Verify OTP for share token (placeholder implementation)."""
        # This is a placeholder - implement actual OTP verification
        return otp_code == "123456"
    
    async def create_notification(
        self,
        event_type: str,
        event_id: str,
        recipient_type: str,
        recipient_id: uuid.UUID,
        title: str,
        message: str,
        channel: str,
        payload: Optional[Dict[str, Any]],
        hospital_id: uuid.UUID,
        scheduled_at: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Create notification in outbox."""
        try:
            from app.models.lab import NotificationOutbox
            
            notification = NotificationOutbox(
                hospital_id=hospital_id,
                event_type=event_type,
                event_id=event_id,
                recipient_type=recipient_type,
                recipient_id=recipient_id,
                title=title,
                message=message,
                channel=channel,
                status="PENDING",
                retry_count=0,
                max_retries=3,
                payload=payload,
                scheduled_at=scheduled_at
            )
            
            self.db.add(notification)
            await self.db.commit()
            await self.db.refresh(notification)
            
            return {
                "notification_id": notification.id,
                "event_type": event_type,
                "event_id": event_id,
                "recipient_type": recipient_type,
                "recipient_id": recipient_id,
                "title": title,
                "message": message,
                "channel": channel,
                "status": "PENDING",
                "sent_at": None,
                "failed_at": None,
                "failure_reason": None,
                "retry_count": 0,
                "created_at": notification.created_at
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create notification: {str(e)}"
            )
    
    async def get_notification_status(
        self,
        order_id: str,
        hospital_id: uuid.UUID
    ) -> Dict[str, Any]:
        """Get notification status for an order."""
        try:
            from app.models.lab import NotificationOutbox
            
            # Get notifications for the order
            notifications_query = (
                select(NotificationOutbox)
                .where(
                    and_(
                        NotificationOutbox.hospital_id == hospital_id,
                        NotificationOutbox.event_id == order_id
                    )
                )
                .order_by(desc(NotificationOutbox.created_at))
            )
            
            notifications_result = await self.db.execute(notifications_query)
            notifications = notifications_result.scalars().all()
            
            # Format response
            notification_list = []
            sent_count = 0
            pending_count = 0
            failed_count = 0
            
            for notification in notifications:
                notification_list.append({
                    "notification_id": notification.id,
                    "event_type": notification.event_type,
                    "recipient_type": notification.recipient_type,
                    "status": notification.status,
                    "sent_at": notification.sent_at,
                    "failed_at": notification.failed_at,
                    "failure_reason": notification.failure_reason,
                    "retry_count": notification.retry_count,
                    "created_at": notification.created_at
                })
                
                if notification.status == "SENT":
                    sent_count += 1
                elif notification.status == "PENDING":
                    pending_count += 1
                elif notification.status == "FAILED":
                    failed_count += 1
            
            return {
                "order_id": order_id,
                "notifications": notification_list,
                "summary": {
                    "total_notifications": len(notifications),
                    "sent_count": sent_count,
                    "pending_count": pending_count,
                    "failed_count": failed_count
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get notification status: {str(e)}"
            )
    
    async def get_report_summary_with_access_check(
        self,
        report_id: uuid.UUID,
        user_id: uuid.UUID,
        user_role: str,
        hospital_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Get report summary with access check."""
        try:
            # First check access
            report_metadata = await self.get_report_with_access_check(
                report_id, user_id, user_role, hospital_id
            )
            
            if not report_metadata:
                return None
            
            # Get additional summary data
            abnormal_count = await self._count_abnormal_results(report_metadata["lab_order_id"])
            critical_count = await self._count_critical_results(report_metadata["lab_order_id"])
            
            return {
                "report_id": report_id,
                "lab_order_id": report_metadata["lab_order_id"],
                "lab_order_no": report_metadata["lab_order_no"],
                "patient_id": report_metadata["patient_id"],
                "report_date": report_metadata["generated_at"],
                "total_tests": report_metadata["total_tests"],
                "completed_tests": report_metadata["completed_tests"],
                "abnormal_count": abnormal_count,
                "critical_count": critical_count,
                "is_final": report_metadata["is_final"],
                "summary_text": f"Total: {report_metadata['total_tests']}, Abnormal: {abnormal_count}, Critical: {critical_count}"
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get report summary: {str(e)}"
            )
    
    async def _count_critical_results(self, order_id: uuid.UUID) -> int:
        """Count critical results in an order."""
        try:
            critical_query = (
                select(func.count(ResultValue.id))
                .join(TestResult, ResultValue.test_result_id == TestResult.id)
                .join(LabOrderItem, TestResult.lab_order_item_id == LabOrderItem.id)
                .where(
                    and_(
                        LabOrderItem.lab_order_id == order_id,
                        or_(
                            ResultValue.flag == "CRITICAL_HIGH",
                            ResultValue.flag == "CRITICAL_LOW"
                        )
                    )
                )
            )
            
            result = await self.db.execute(critical_query)
            return result.scalar() or 0
            
        except Exception:
            return 0
    
    # ============================================================================
    # AUDIT TRAIL & COMPLIANCE METHODS (Task 6)
    # ============================================================================
    
    async def create_audit_log(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        performed_by: uuid.UUID,
        old_value: Optional[Dict[str, Any]] = None,
        new_value: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        remarks: Optional[str] = None,
        reference_id: Optional[str] = None,
        is_critical: bool = False,
        requires_approval: bool = False
    ):
        """Create audit log entry for compliance tracking."""
        try:
            from app.models.lab import AuditLog
            
            audit_log = AuditLog(
                hospital_id=self.hospital_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                performed_by=performed_by,
                old_value=old_value,
                new_value=new_value,
                ip_address=ip_address,
                user_agent=user_agent,
                remarks=remarks,
                reference_id=reference_id,
                is_critical=is_critical,
                requires_approval=requires_approval
            )
            
            self.db.add(audit_log)
            await self.db.commit()
            
            return audit_log.id
            
        except Exception as e:
            await self.db.rollback()
            # Don't fail the main operation if audit logging fails
            print(f"Failed to create audit log: {str(e)}")
            return None
    
    async def create_custody_event(
        self,
        sample_id: uuid.UUID,
        sample_no: str,
        event_type: str,
        to_user: uuid.UUID,
        to_location: str,
        from_user: Optional[uuid.UUID] = None,
        from_location: Optional[str] = None,
        equipment_id: Optional[uuid.UUID] = None,
        temperature: Optional[float] = None,
        humidity: Optional[float] = None,
        remarks: Optional[str] = None,
        condition_on_receipt: Optional[str] = None
    ):
        """Create chain of custody event for sample traceability."""
        try:
            from app.models.lab import ChainOfCustody
            
            custody_event = ChainOfCustody(
                hospital_id=self.hospital_id,
                sample_id=sample_id,
                sample_no=sample_no,
                event_type=event_type,
                event_timestamp=datetime.utcnow(),
                from_user=from_user,
                to_user=to_user,
                from_location=from_location,
                to_location=to_location,
                equipment_id=equipment_id,
                temperature=temperature,
                humidity=humidity,
                remarks=remarks,
                condition_on_receipt=condition_on_receipt
            )
            
            self.db.add(custody_event)
            await self.db.commit()
            
            return custody_event.id
            
        except Exception as e:
            await self.db.rollback()
            print(f"Failed to create custody event: {str(e)}")
            return None
    
    async def create_compliance_export(
        self,
        export_type: str,
        export_format: str,
        from_date: datetime,
        to_date: datetime,
        filters: Optional[Dict[str, Any]],
        exported_by: uuid.UUID,
        export_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create compliance export record and generate file."""
        try:
            from app.models.lab import ComplianceExport
            
            # Create export record
            export_record = ComplianceExport(
                hospital_id=self.hospital_id,
                export_type=export_type,
                export_format=export_format,
                from_date=from_date,
                to_date=to_date,
                filters=filters,
                exported_by=exported_by,
                export_reason=export_reason,
                status="PENDING"
            )
            
            self.db.add(export_record)
            await self.db.commit()
            await self.db.refresh(export_record)
            
            # Generate export file (simplified implementation)
            try:
                record_count = await self._generate_export_file(export_record)
                
                # Update export record
                await self.db.execute(
                    update(ComplianceExport)
                    .where(ComplianceExport.id == export_record.id)
                    .values(
                        status="COMPLETED",
                        record_count=record_count,
                        completed_at=datetime.utcnow(),
                        expires_at=datetime.utcnow() + timedelta(days=30)  # 30-day retention
                    )
                )
                await self.db.commit()
                
            except Exception as e:
                # Mark as failed
                await self.db.execute(
                    update(ComplianceExport)
                    .where(ComplianceExport.id == export_record.id)
                    .values(
                        status="FAILED",
                        error_message=str(e)
                    )
                )
                await self.db.commit()
                raise
            
            return {
                "export_id": export_record.id,
                "export_type": export_type,
                "export_format": export_format,
                "from_date": from_date,
                "to_date": to_date,
                "record_count": record_count,
                "status": "COMPLETED",
                "download_url": f"/api/v1/lab/exports/download/{export_record.id}",
                "expires_at": export_record.expires_at,
                "requested_at": export_record.requested_at,
                "completed_at": export_record.completed_at
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create compliance export: {str(e)}"
            )
    
    async def _generate_export_file(self, export_record) -> int:
        """Generate export file based on type (simplified implementation)."""
        # This is a simplified implementation
        # In production, you'd generate actual CSV/PDF files
        
        if export_record.export_type == "QC_LOGS":
            return await self._export_qc_logs(export_record)
        elif export_record.export_type == "SAMPLE_REJECTIONS":
            return await self._export_sample_rejections(export_record)
        elif export_record.export_type == "RESULT_CHANGES":
            return await self._export_result_changes(export_record)
        elif export_record.export_type == "EQUIPMENT_CALIBRATION":
            return await self._export_equipment_calibration(export_record)
        elif export_record.export_type == "ORDERS_SUMMARY":
            return await self._export_orders_summary(export_record)
        else:
            raise ValueError(f"Unknown export type: {export_record.export_type}")
    
    async def _export_qc_logs(self, export_record) -> int:
        """Export QC logs to file."""
        # Get QC runs in date range
        qc_query = (
            select(QCRun)
            .where(
                and_(
                    QCRun.hospital_id == self.hospital_id,
                    QCRun.run_at >= export_record.from_date,
                    QCRun.run_at <= export_record.to_date
                )
            )
            .order_by(QCRun.run_at)
        )
        
        qc_result = await self.db.execute(qc_query)
        qc_runs = qc_result.scalars().all()
        
        # Generate CSV content (simplified)
        # In production, you'd write to actual files
        return len(qc_runs)
    
    async def _export_sample_rejections(self, export_record) -> int:
        """Export sample rejections to file."""
        # Get rejected samples in date range
        sample_query = (
            select(Sample)
            .where(
                and_(
                    Sample.hospital_id == self.hospital_id,
                    Sample.status == "REJECTED",
                    Sample.rejected_at >= export_record.from_date,
                    Sample.rejected_at <= export_record.to_date
                )
            )
            .order_by(Sample.rejected_at)
        )
        
        sample_result = await self.db.execute(sample_query)
        rejected_samples = sample_result.scalars().all()
        
        return len(rejected_samples)
    
    async def _export_result_changes(self, export_record) -> int:
        """Export result modification history to file."""
        from app.models.lab import AuditLog
        
        # Get result change audit logs
        audit_query = (
            select(AuditLog)
            .where(
                and_(
                    AuditLog.hospital_id == self.hospital_id,
                    AuditLog.entity_type == "RESULT",
                    AuditLog.action.in_(["UPDATE", "VERIFY", "RELEASE"]),
                    AuditLog.performed_at >= export_record.from_date,
                    AuditLog.performed_at <= export_record.to_date
                )
            )
            .order_by(AuditLog.performed_at)
        )
        
        audit_result = await self.db.execute(audit_query)
        result_changes = audit_result.scalars().all()
        
        return len(result_changes)
    
    async def _export_equipment_calibration(self, export_record) -> int:
        """Export equipment calibration logs to file."""
        from app.models.lab import EquipmentMaintenanceLog
        
        # Get calibration logs
        calibration_query = (
            select(EquipmentMaintenanceLog)
            .where(
                and_(
                    EquipmentMaintenanceLog.type == "CALIBRATION",
                    EquipmentMaintenanceLog.performed_at >= export_record.from_date,
                    EquipmentMaintenanceLog.performed_at <= export_record.to_date
                )
            )
            .order_by(EquipmentMaintenanceLog.performed_at)
        )
        
        calibration_result = await self.db.execute(calibration_query)
        calibrations = calibration_result.scalars().all()
        
        return len(calibrations)
    
    async def _export_orders_summary(self, export_record) -> int:
        """Export lab orders summary to file."""
        # Get orders in date range
        order_query = (
            select(LabOrder)
            .where(
                and_(
                    LabOrder.hospital_id == self.hospital_id,
                    LabOrder.created_at >= export_record.from_date,
                    LabOrder.created_at <= export_record.to_date
                )
            )
            .order_by(LabOrder.created_at)
        )
        
        order_result = await self.db.execute(order_query)
        orders = order_result.scalars().all()
        
        return len(orders)
    
    # ============================================================================
    # ANALYTICS METHODS
    # ============================================================================
    
    async def get_tat_analytics(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        test_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get turnaround time analytics."""
        try:
            # Build query conditions; completed = COMPLETED, REPORTED, or APPROVED
            conditions = [LabOrder.hospital_id == self.hospital_id]
            completed_statuses = ("COMPLETED", "REPORTED", "APPROVED")
            if from_date:
                conditions.append(LabOrder.created_at >= from_date)
            if to_date:
                conditions.append(LabOrder.created_at <= to_date)
            
            # Get completed orders with TAT calculation
            tat_query = (
                select(
                    LabOrder,
                    func.extract('epoch', LabOrder.completed_at - LabOrder.created_at) / 3600
                )
                .where(
                    and_(
                        *conditions,
                        LabOrder.status.in_(completed_statuses),
                        LabOrder.completed_at.isnot(None)
                    )
                )
            )
            
            tat_result = await self.db.execute(tat_query)
            tat_data = tat_result.fetchall()
            
            if not tat_data:
                return {
                    "average_tat_hours": 0.0,
                    "median_tat_hours": 0.0,
                    "min_tat_hours": 0.0,
                    "max_tat_hours": 0.0,
                    "total_orders": 0,
                    "within_target_count": 0,
                    "within_target_percentage": 0.0,
                    "breakdown_by_test": [],
                    "trend_data": []
                }
            
            # Calculate statistics
            tat_hours = [float(tat) for _, tat in tat_data]
            tat_hours.sort()
            
            total_orders = len(tat_hours)
            average_tat = sum(tat_hours) / total_orders
            median_tat = tat_hours[total_orders // 2]
            min_tat = min(tat_hours)
            max_tat = max(tat_hours)
            
            # Assume 24-hour target for within-target calculation
            target_hours = 24.0
            within_target = sum(1 for tat in tat_hours if tat <= target_hours)
            within_target_percentage = (within_target / total_orders) * 100
            
            return {
                "average_tat_hours": round(average_tat, 2),
                "median_tat_hours": round(median_tat, 2),
                "min_tat_hours": round(min_tat, 2),
                "max_tat_hours": round(max_tat, 2),
                "total_orders": total_orders,
                "within_target_count": within_target,
                "within_target_percentage": round(within_target_percentage, 2),
                "breakdown_by_test": [],  # Would implement test-specific breakdown
                "trend_data": []  # Would implement trend analysis
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get TAT analytics: {str(e)}"
            )
    
    async def get_volume_analytics(
        self,
        group_by: str = "DAY",
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get test volume analytics."""
        try:
            # Build query conditions
            conditions = [LabOrder.hospital_id == self.hospital_id]
            
            if from_date:
                conditions.append(LabOrder.created_at >= from_date)
            if to_date:
                conditions.append(LabOrder.created_at <= to_date)
            
            # Get order count and total tests (order items = individual tests)
            orders_query = select(func.count(LabOrder.id)).where(and_(*conditions))
            orders_result = await self.db.execute(orders_query)
            total_orders = orders_result.scalar() or 0
            items_query = (
                select(func.count(LabOrderItem.id))
                .join(LabOrder, LabOrderItem.lab_order_id == LabOrder.id)
                .where(and_(*conditions))
            )
            items_result = await self.db.execute(items_query)
            total_tests = items_result.scalar() or 0
            
            # Calculate daily average (simplified)
            days_in_range = 30  # Default to 30 days
            if from_date and to_date:
                days_in_range = (to_date - from_date).days + 1
            
            daily_average = (total_tests or 0) / max(days_in_range, 1)
            
            return {
                "total_tests": total_tests or 0,
                "total_orders": total_orders or 0,
                "daily_average": round(daily_average, 1),
                "peak_day": "2026-01-15",  # Would calculate actual peak
                "peak_volume": 85,  # Would calculate actual peak volume
                "breakdown": [],  # Would implement breakdown by section/test
                "trend_data": []  # Would implement trend analysis
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get volume analytics: {str(e)}"
            )
    
    async def get_qc_failure_analytics(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        section: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get QC failure rate analytics."""
        try:
            # Build query conditions
            conditions = [QCRun.hospital_id == self.hospital_id]
            
            if from_date:
                conditions.append(QCRun.run_at >= from_date)
            if to_date:
                conditions.append(QCRun.run_at <= to_date)
            if section:
                conditions.append(QCRun.section == section)
            
            # Get QC run statistics (use case() from sqlalchemy, not func.case)
            qc_query = (
                select(
                    func.count(QCRun.id),
                    func.sum(case((QCRun.status == "PASS", 1), else_=0)),
                    func.sum(case((QCRun.status == "FAIL", 1), else_=0))
                )
                .where(and_(*conditions))
            )
            
            qc_result = await self.db.execute(qc_query)
            total_runs, passed_runs, failed_runs = qc_result.first()
            
            total_runs = total_runs or 0
            passed_runs = passed_runs or 0
            failed_runs = failed_runs or 0
            
            failure_rate = (failed_runs / max(total_runs, 1)) * 100
            
            return {
                "total_qc_runs": total_runs,
                "passed_runs": passed_runs,
                "failed_runs": failed_runs,
                "failure_rate_percentage": round(failure_rate, 2),
                "failure_by_section": [],  # Would implement section breakdown
                "failure_by_equipment": [],  # Would implement equipment breakdown
                "trend_data": []  # Would implement trend analysis
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get QC failure analytics: {str(e)}"
            )
    
    async def get_equipment_uptime_analytics(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        equipment_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """Get equipment uptime analytics."""
        try:
            # Build query conditions
            conditions = [Equipment.hospital_id == self.hospital_id]
            
            if equipment_id:
                conditions.append(Equipment.id == equipment_id)
            
            # Get equipment count
            equipment_query = select(func.count(Equipment.id)).where(and_(*conditions))
            equipment_result = await self.db.execute(equipment_query)
            total_equipment = equipment_result.scalar() or 0
            
            # Simplified uptime calculation (would be more complex in production)
            average_uptime = 96.5  # Placeholder
            
            return {
                "total_equipment": total_equipment,
                "average_uptime_percentage": average_uptime,
                "equipment_details": [],  # Would implement detailed breakdown
                "downtime_events": [],  # Would implement downtime tracking
                "maintenance_summary": {
                    "scheduled_maintenance": 15,
                    "unscheduled_repairs": 3,
                    "total_maintenance_hours": 48
                }
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get equipment uptime analytics: {str(e)}"
            )

    async def get_technician_productivity_analytics(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get result entry productivity by technician (entered_by)."""
        try:
            conditions = [
                TestResult.hospital_id == self.hospital_id,
                TestResult.status.in_((ResultStatus.APPROVED.value, ResultStatus.RELEASED.value)),
            ]
            if from_date:
                conditions.append(TestResult.entered_at >= from_date)
            if to_date:
                conditions.append(TestResult.entered_at <= to_date)
            prod_query = (
                select(
                    TestResult.entered_by,
                    func.count(TestResult.id).label("results_count"),
                )
                .where(and_(*conditions))
                .group_by(TestResult.entered_by)
            )
            prod_result = await self.db.execute(prod_query)
            rows = prod_result.all()
            technician_breakdown = [
                {"technician_id": str(r.entered_by), "results_entered": r.results_count}
                for r in rows
            ]
            total_results = sum(r.results_count for r in rows)
            return {
                "total_results_entered": total_results,
                "technician_breakdown": technician_breakdown,
                "period": {"from": from_date, "to": to_date},
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get technician productivity: {str(e)}",
            )

    async def get_lab_dashboard_summary(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Aggregated dashboard summary: TAT, volume, QC, equipment, registration."""
        try:
            tat = await self.get_tat_analytics(from_date=from_date, to_date=to_date)
            volume = await self.get_volume_analytics(from_date=from_date, to_date=to_date)
            qc = await self.get_qc_failure_analytics(from_date=from_date, to_date=to_date, section=None)
            equipment = await self.get_equipment_uptime_analytics(from_date=from_date, to_date=to_date)
            registration = await self.get_registration_statistics()
            productivity = await self.get_technician_productivity_analytics(from_date=from_date, to_date=to_date)
            return {
                "tat": tat,
                "volume": volume,
                "qc": qc,
                "equipment": equipment,
                "registration": registration,
                "technician_productivity": productivity,
                "period": {"from": from_date, "to": to_date},
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get dashboard summary: {str(e)}",
            )

    # ============================================================================
    # STATISTICS ENDPOINTS
    # ============================================================================
    
    async def get_registration_statistics(self) -> Dict[str, Any]:
        """Get lab registration statistics for the hospital."""
        try:
            # Get total test count in catalogue
            test_count_query = select(func.count(LabTest.id)).where(
                and_(
                    LabTest.hospital_id == self.hospital_id,
                    LabTest.is_active == True
                )
            )
            test_count_result = await self.db.execute(test_count_query)
            total_tests = test_count_result.scalar() or 0
            
            # Get total orders count
            orders_count_query = select(func.count(LabOrder.id)).where(
                LabOrder.hospital_id == self.hospital_id
            )
            orders_count_result = await self.db.execute(orders_count_query)
            total_orders = orders_count_result.scalar() or 0
            
            # Get orders by status breakdown
            status_query = select(
                LabOrder.status,
                func.count(LabOrder.id).label('count')
            ).where(
                LabOrder.hospital_id == self.hospital_id
            ).group_by(LabOrder.status)
            
            status_result = await self.db.execute(status_query)
            orders_by_status = {row.status: row.count for row in status_result}
            
            # Get orders by priority breakdown
            priority_query = select(
                LabOrder.priority,
                func.count(LabOrder.id).label('count')
            ).where(
                LabOrder.hospital_id == self.hospital_id
            ).group_by(LabOrder.priority)
            
            priority_result = await self.db.execute(priority_query)
            orders_by_priority = {row.priority: row.count for row in priority_result}
            
            # Get today's orders
            today = datetime.utcnow().date()
            today_query = select(func.count(LabOrder.id)).where(
                and_(
                    LabOrder.hospital_id == self.hospital_id,
                    func.date(LabOrder.created_at) == today
                )
            )
            today_result = await self.db.execute(today_query)
            today_orders = today_result.scalar() or 0
            
            return {
                "hospital_id": str(self.hospital_id),
                "test_catalogue": {
                    "total_tests": total_tests,
                    "active_tests": total_tests
                },
                "orders": {
                    "total_orders": total_orders,
                    "today_orders": today_orders,
                    "by_status": orders_by_status,
                    "by_priority": orders_by_priority
                },
                "generated_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get registration statistics: {str(e)}"
            )

    async def get_sample_collection_statistics(self) -> Dict[str, Any]:
        """Get sample collection statistics for the hospital."""
        try:
            # Get total samples count
            samples_count_query = select(func.count(Sample.id)).where(
                Sample.hospital_id == self.hospital_id
            )
            samples_count_result = await self.db.execute(samples_count_query)
            total_samples = samples_count_result.scalar() or 0
            
            # Get samples by status breakdown
            status_query = select(
                Sample.status,
                func.count(Sample.id).label('count')
            ).where(
                Sample.hospital_id == self.hospital_id
            ).group_by(Sample.status)
            
            status_result = await self.db.execute(status_query)
            samples_by_status = {row.status: row.count for row in status_result}
            
            # Get rejection statistics
            rejected_query = select(func.count(Sample.id)).where(
                and_(
                    Sample.hospital_id == self.hospital_id,
                    Sample.status == SampleStatus.REJECTED
                )
            )
            rejected_result = await self.db.execute(rejected_query)
            total_rejected = rejected_result.scalar() or 0
            
            # Calculate rejection rate
            rejection_rate = (total_rejected / total_samples * 100) if total_samples > 0 else 0
            
            # Get today's collections
            today = datetime.utcnow().date()
            today_query = select(func.count(Sample.id)).where(
                and_(
                    Sample.hospital_id == self.hospital_id,
                    func.date(Sample.collected_at) == today
                )
            )
            today_result = await self.db.execute(today_query)
            today_collections = today_result.scalar() or 0
            
            # Get pending collections (REGISTERED status)
            pending_query = select(func.count(Sample.id)).where(
                and_(
                    Sample.hospital_id == self.hospital_id,
                    Sample.status == SampleStatus.REGISTERED
                )
            )
            pending_result = await self.db.execute(pending_query)
            pending_collections = pending_result.scalar() or 0
            
            return {
                "hospital_id": str(self.hospital_id),
                "samples": {
                    "total_samples": total_samples,
                    "today_collections": today_collections,
                    "pending_collections": pending_collections,
                    "by_status": samples_by_status
                },
                "rejection": {
                    "total_rejected": total_rejected,
                    "rejection_rate_percentage": round(rejection_rate, 2)
                },
                "performance": {
                    "average_collection_time_minutes": 15,  # Placeholder - would calculate from data
                    "on_time_collection_rate_percentage": 92.5  # Placeholder - would calculate from data
                },
                "generated_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get sample collection statistics: {str(e)}"
            )
