"""
Prescription API: visiting patients (pharmacy integration + dispense) and online patients (PDF download).

- Visiting: Doctor creates prescription using REAL pharmacy medicines → Pharmacist dispenses → Patient can download PDF.
- Online: Same create (pharmacy medicines); no dispense at hospital; Patient downloads PDF.

Endpoints: doctor (search medicines, create, list) | pharmacist (list, dispense) | common (detail, PDF) | patient (list my prescriptions).
"""
import uuid
import logging
from typing import List, Optional
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field, validator

from app.core.database import get_db_session, AsyncSessionLocal
from app.core.security import get_current_user
from app.services.prescription_notification_service import (
    notify_prescription_submitted,
    notify_prescription_dispensed,
)
from app.models.user import User
from app.models.patient import PatientProfile, Appointment
from app.models.doctor import DoctorProfile, Prescription
from decimal import Decimal
from app.models.pharmacy import Medicine, StockBatch, StockLedger
from app.models.tenant import Hospital
from app.core.enums import UserRole
from app.services.prescription_pdf_service import generate_prescription_pdf

router = APIRouter(prefix="/simple-prescription", tags=["Prescription (visiting + online, PDF)"])
_logger = logging.getLogger(__name__)


async def _notify_prescription_submitted_task(prescription_id: str, hospital_id: str) -> None:
    """Background: create in-app notifications for Patient, Receptionist, Pharmacy (non-blocking)."""
    try:
        async with AsyncSessionLocal() as session:
            await notify_prescription_submitted(
                session, uuid.UUID(prescription_id), uuid.UUID(hospital_id)
            )
            await session.commit()
    except Exception as e:
        _logger.warning("Prescription submit notification failed: %s", e, exc_info=True)


async def _notify_prescription_dispensed_task(prescription_id: str, hospital_id: str) -> None:
    """Background: create in-app notifications for Patient (and Receptionist) when dispensed (non-blocking)."""
    try:
        async with AsyncSessionLocal() as session:
            await notify_prescription_dispensed(
                session, uuid.UUID(prescription_id), uuid.UUID(hospital_id)
            )
            await session.commit()
    except Exception as e:
        _logger.warning("Prescription dispensed notification failed: %s", e, exc_info=True)


# ============================================================================
# SCHEMAS
# ============================================================================

class MedicineSearchResult(BaseModel):
    """Doctor medicine search: pharmacy medicines with stock status (Iteration 1)."""
    medicine_id: str
    medicine_code: Optional[str] = None  # pharmacy Medicine.sku
    brand_name: Optional[str] = None
    generic_name: str
    strength: Optional[str] = None
    dosage_form: str
    manufacturer: Optional[str] = None
    category: Optional[str] = None
    total_stock: int = 0  # backward compat; same as available_qty
    is_available: bool = False
    stock_status: str = "OUT_OF_STOCK"  # IN_STOCK | LOW_STOCK | OUT_OF_STOCK
    available_qty: int = 0  # sum of (qty_on_hand - qty_reserved) for non-expired batches
    soonest_expiry_date: Optional[str] = None  # YYYY-MM-DD min expiry among batches with qty


class MedicationTimingSchema(BaseModel):
    """When to take: morning/afternoon/night and/or specific times."""
    morning: bool = False
    afternoon: bool = False
    night: bool = False
    times: Optional[List[str]] = Field(None, description="e.g. ['08:00', '14:00', '20:00']")


class PrescriptionMedicineCreate(BaseModel):
    """Structured prescription item: full directions for PDF; no mandatory pharmacy ID."""
    medicine_name: str = Field(..., description="Medicine name (brand or generic, free text)")
    quantity: int = Field(..., description="Total quantity to dispense (requested_qty)", gt=0)
    dosage_text: str = Field(..., description="e.g. '1 tablet', '5ml'")
    frequency: str = Field(..., description="e.g. BD, TID, QID, OD, SOS, twice daily")
    timing: Optional[MedicationTimingSchema] = Field(None, description="Morning/afternoon/night or times")
    before_food: bool = Field(False, description="Take before food")
    after_food: bool = Field(False, description="Take after food")
    duration_days: int = Field(..., description="Duration in days", gt=0)
    route: str = Field("ORAL", description="ORAL, TOPICAL, INHALATION, IV, etc.")
    instructions: Optional[str] = Field(None, description="Free text e.g. 'After food for 5 days'")

    @validator("after_food")
    def reject_before_and_after_food(cls, v, values):
        if v and values.get("before_food"):
            raise ValueError("before_food and after_food cannot both be true")
        return v


class SimplePrescriptionCreate(BaseModel):
    patient_ref: str = Field(..., description="Patient reference number")
    diagnosis: str = Field(..., description="Clinical diagnosis")
    symptoms: Optional[str] = Field(None, description="Patient symptoms")
    medicines: List[PrescriptionMedicineCreate] = Field(..., min_items=1)
    general_instructions: Optional[str] = None
    diet_instructions: Optional[str] = None
    follow_up_date: Optional[str] = Field(None, pattern="^\\d{4}-\\d{2}-\\d{2}$")


class PrescriptionResponse(BaseModel):
    prescription_id: str
    prescription_number: str
    patient_ref: str
    patient_name: str
    doctor_name: str
    prescription_date: str
    diagnosis: str
    total_medicines: int
    is_dispensed: bool
    created_at: str


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_user_context(current_user: User) -> dict:
    """Extract user context"""
    user_roles = [role.name for role in current_user.roles]
    return {
        "user_id": str(current_user.id),
        "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
        "role": user_roles[0] if user_roles else None,
        "all_roles": user_roles
    }


async def get_doctor_profile(user_context: dict, db: AsyncSession):
    """Get doctor profile"""
    if user_context["role"] != UserRole.DOCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Doctor role required"
        )

    doctor_user_id = uuid.UUID(user_context["user_id"])

    result = await db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == doctor_user_id)
        .options(
            selectinload(DoctorProfile.user),
            selectinload(DoctorProfile.department)
        )
    )
    
    doctor = result.scalar_one_or_none()
    
    if not doctor:
        # Create mock profile if doesn't exist
        from app.models.hospital import StaffDepartmentAssignment
        
        doctor_result = await db.execute(
            select(User).where(User.id == doctor_user_id)
        )
        doctor_user = doctor_result.scalar_one_or_none()
        
        if not doctor_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor user not found"
            )
        
        assignment_result = await db.execute(
            select(StaffDepartmentAssignment)
            .where(StaffDepartmentAssignment.staff_id == doctor_user_id)
            .options(selectinload(StaffDepartmentAssignment.department))
        )
        assignment = assignment_result.scalar_one_or_none()
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor not assigned to any department"
            )
        
        # Ensure hospital_id is available
        if not user_context.get("hospital_id"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hospital ID is required. Doctor must be associated with a hospital."
            )
        
        # Create DoctorProfile
        doctor = DoctorProfile(
            hospital_id=uuid.UUID(user_context["hospital_id"]),
            user_id=doctor_user.id,
            department_id=assignment.department.id,
            doctor_id=f"DOC-{doctor_user.id}",
            medical_license_number=f"LIC-{doctor_user.id}",
            designation="General Practitioner",
            specialization=assignment.department.name or "General Medicine",
            experience_years=5,
            qualifications=["MBBS"],
            consultation_fee=500.00,
            follow_up_fee=300.00,
            is_available_for_emergency=True,
            is_accepting_new_patients=True,
            bio=f"Experienced doctor in {assignment.department.name}",
            languages_spoken=["English"]
        )
        db.add(doctor)
        await db.commit()
        await db.refresh(doctor)
    
    return doctor


def generate_prescription_number() -> str:
    """Generate unique prescription number"""
    import random
    import string
    year = datetime.now().year
    random_part = ''.join(random.choices(string.digits, k=6))
    return f"RX-{year}-{random_part}"


# ============================================================================
# DOCTOR ENDPOINTS - Search Real Medicines
# ============================================================================

def _stock_status(available_qty: int, reorder_level: Optional[int]) -> str:
    """IN_STOCK | LOW_STOCK | OUT_OF_STOCK. LOW when available_qty <= reorder_level."""
    if available_qty <= 0:
        return "OUT_OF_STOCK"
    threshold = (reorder_level if reorder_level is not None else 10)
    if available_qty <= threshold:
        return "LOW_STOCK"
    return "IN_STOCK"


@router.get("/doctor/medicines/search", response_model=List[MedicineSearchResult])
async def search_real_medicines(
    query: str = Query(..., min_length=2, description="Search by brand name, generic name, or SKU"),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Search pharmacy medicines with stock availability.
    
    Access Control:
    - **Who can access:** Doctors only
    """
    user_context = get_user_context(current_user)
    doctor = await get_doctor_profile(user_context, db)
    hospital_uuid = uuid.UUID(user_context["hospital_id"])
    query_lower = f"%{query.lower()}%"

    # Search pharmacy_medicines (Medicine is_active; search generic_name, brand_name, sku)
    search_conditions = [
        Medicine.hospital_id == hospital_uuid,
        Medicine.is_active == True,
        or_(
            func.lower(Medicine.generic_name).like(query_lower),
            func.lower(func.coalesce(Medicine.brand_name, "")).like(query_lower),
            func.lower(func.coalesce(Medicine.sku, "")).like(query_lower),
        ),
    ]
    medicines_result = await db.execute(
        select(Medicine).where(and_(*search_conditions)).limit(limit)
    )
    medicines = medicines_result.scalars().all()

    results = []
    today = date.today()
    for medicine in medicines:
        # Available = sum(qty_on_hand - qty_reserved) for non-expired batches
        stock_result = await db.execute(
            select(
                func.coalesce(func.sum(StockBatch.qty_on_hand - StockBatch.qty_reserved), 0).label("available"),
                func.min(StockBatch.expiry_date).label("soonest_expiry"),
            ).where(
                and_(
                    StockBatch.medicine_id == medicine.id,
                    StockBatch.hospital_id == hospital_uuid,
                    StockBatch.expiry_date > today,
                    StockBatch.qty_on_hand > 0,
                )
            )
        )
        row = stock_result.one()
        available_qty = int(row.available) if row.available is not None else 0
        soonest_expiry = row.soonest_expiry
        reorder_level = getattr(medicine, "reorder_level", None)
        if reorder_level is not None and hasattr(reorder_level, "__int__"):
            reorder_level = int(reorder_level)
        stock_status = _stock_status(available_qty, reorder_level)

        results.append(MedicineSearchResult(
            medicine_id=str(medicine.id),
            medicine_code=medicine.sku if getattr(medicine, "sku", None) else None,
            brand_name=medicine.brand_name,
            generic_name=medicine.generic_name,
            strength=medicine.strength,
            dosage_form=medicine.dosage_form,
            manufacturer=medicine.manufacturer,
            category=medicine.category,
            total_stock=available_qty,
            is_available=available_qty > 0,
            stock_status=stock_status,
            available_qty=available_qty,
            soonest_expiry_date=soonest_expiry.isoformat() if soonest_expiry else None,
        ))
    return results


# ============================================================================
# DOCTOR ENDPOINTS - Create Prescription
# ============================================================================

@router.post("/doctor/prescriptions/create", response_model=PrescriptionResponse)
async def create_simple_prescription(
    prescription_data: SimplePrescriptionCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create prescription using REAL pharmacy medicines.
    On submit: notifies Patient, Receptionist, Pharmacy (in-app; non-blocking).
    
    Access Control:
    - **Who can access:** Doctors only
    
    Workflow:
    1. Validate patient exists
    2. Validate all medicines exist in pharmacy inventory
    3. Create prescription with real medicine data
    4. Return prescription details
    """
    user_context = get_user_context(current_user)
    doctor = await get_doctor_profile(user_context, db)
    
    # Ensure hospital_id is available
    if not user_context.get("hospital_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital ID is required. Doctor must be associated with a hospital."
        )
    
    # Get patient
    patient_result = await db.execute(
        select(PatientProfile)
        .where(
            and_(
                PatientProfile.patient_id == prescription_data.patient_ref,
                PatientProfile.hospital_id == uuid.UUID(user_context["hospital_id"])
            )
        )
        .options(selectinload(PatientProfile.user))
    )
    
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient not found: {prescription_data.patient_ref}"
        )
    
    # Validate medicines: if medicine_id is provided, enforce pharmacy + stock;
    # if only medicine_name is provided, accept as free-text (no stock check).
    hospital_uuid = uuid.UUID(user_context["hospital_id"])
    today = date.today()
    medications_json = []
    for med in prescription_data.medicines:
        timing_json = med.timing.dict() if med.timing is not None else None

        medicine_id = getattr(med, "medicine_id", None)
        if medicine_id:
            # Linked to pharmacy medicine: validate and check stock
            medicine_result = await db.execute(
                select(Medicine).where(
                    and_(
                        Medicine.id == uuid.UUID(medicine_id),
                        Medicine.hospital_id == hospital_uuid,
                        Medicine.is_active == True,
                    )
                )
            )
            medicine = medicine_result.scalar_one_or_none()
            if not medicine:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Medicine not found or inactive: {medicine_id}",
                )
            # Available = sum(qty_on_hand - qty_reserved) for non-expired batches
            stock_result = await db.execute(
                select(func.coalesce(func.sum(StockBatch.qty_on_hand - StockBatch.qty_reserved), 0)).where(
                    and_(
                        StockBatch.medicine_id == medicine.id,
                        StockBatch.hospital_id == hospital_uuid,
                        StockBatch.expiry_date > today,
                        StockBatch.qty_on_hand > 0,
                    )
                )
            )
            total_stock = int(stock_result.scalar() or 0)
            if total_stock < med.quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient stock for {medicine.brand_name or medicine.generic_name}. Available: {total_stock}, Required: {med.quantity}",
                )

            medications_json.append(
                {
                    "medicine_id": str(medicine.id),
                    "medicine_code": getattr(medicine, "sku", None),
                    "brand_name": medicine.brand_name,
                    "generic_name": medicine.generic_name,
                    "strength": medicine.strength,
                    "dosage_form": medicine.dosage_form,
                    "manufacturer": medicine.manufacturer,
                    "quantity": med.quantity,
                    "dosage_text": med.dosage_text,
                    "frequency": med.frequency,
                    "timing": timing_json,
                    "before_food": med.before_food,
                    "after_food": med.after_food,
                    "duration_days": med.duration_days,
                    "route": med.route,
                    "instructions": med.instructions,
                }
            )
        else:
            # Free-text medicine (no pharmacy record / stock check)
            medications_json.append(
                {
                    "medicine_id": None,
                    "medicine_code": None,
                    "brand_name": med.medicine_name,
                    "generic_name": med.medicine_name,
                    "strength": None,
                    "dosage_form": None,
                    "manufacturer": None,
                    "quantity": med.quantity,
                    "dosage_text": med.dosage_text,
                    "frequency": med.frequency,
                    "timing": timing_json,
                    "before_food": med.before_food,
                    "after_food": med.after_food,
                    "duration_days": med.duration_days,
                    "route": med.route,
                    "instructions": med.instructions,
                }
            )
    
    # Generate prescription number
    prescription_number = generate_prescription_number()
    
    # Create prescription
    prescription = Prescription(
        patient_id=patient.id,
        doctor_id=doctor.id,
        hospital_id=uuid.UUID(user_context["hospital_id"]),
        prescription_number=prescription_number,
        prescription_date=date.today().isoformat(),
        diagnosis=prescription_data.diagnosis,
        symptoms=prescription_data.symptoms,
        medications=medications_json,
        general_instructions=prescription_data.general_instructions,
        diet_instructions=prescription_data.diet_instructions,
        follow_up_date=prescription_data.follow_up_date,
        is_dispensed=False,
        is_digitally_signed=True,
        signature_hash=f"hash_{prescription_number}"
    )
    
    db.add(prescription)
    await db.commit()
    await db.refresh(prescription)

    # Notify Patient, Receptionist, Pharmacy (event-driven; do not block response)
    if user_context.get("hospital_id"):
        background_tasks.add_task(
            _notify_prescription_submitted_task,
            str(prescription.id),
            user_context["hospital_id"],
        )
    
    return PrescriptionResponse(
        prescription_id=str(prescription.id),
        prescription_number=prescription.prescription_number,
        patient_ref=patient.patient_id,
        patient_name=f"{patient.user.first_name} {patient.user.last_name}",
        doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        prescription_date=prescription.prescription_date,
        diagnosis=prescription.diagnosis,
        total_medicines=len(medications_json),
        is_dispensed=False,
        created_at=prescription.created_at.isoformat()
    )


# ============================================================================
# DOCTOR ENDPOINTS - View Own Prescriptions
# ============================================================================

@router.get("/doctor/prescriptions", response_model=List[PrescriptionResponse])
async def get_doctor_prescriptions(
    patient_ref: Optional[str] = Query(None, description="Filter by patient reference"),
    is_dispensed: Optional[bool] = Query(None, description="Filter by dispensed status"),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get doctor's prescriptions with filtering.
    
    Access Control:
    - **Who can access:** Doctors only (own prescriptions)
    """
    user_context = get_user_context(current_user)
    doctor = await get_doctor_profile(user_context, db)
    
    # Build query
    conditions = [Prescription.doctor_id == doctor.id]
    
    if patient_ref:
        patient_result = await db.execute(
            select(PatientProfile.id)
            .where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == user_context["hospital_id"]
                )
            )
        )
        patient_id = patient_result.scalar_one_or_none()
        if patient_id:
            conditions.append(Prescription.patient_id == patient_id)
    
    if is_dispensed is not None:
        conditions.append(Prescription.is_dispensed == is_dispensed)
    
    # Get prescriptions
    prescriptions_result = await db.execute(
        select(Prescription)
        .where(and_(*conditions))
        .options(selectinload(Prescription.patient).selectinload(PatientProfile.user))
        .order_by(desc(Prescription.created_at))
        .limit(limit)
    )
    
    prescriptions = prescriptions_result.scalars().all()
    
    # Format response
    results = []
    for prescription in prescriptions:
        results.append(PrescriptionResponse(
            prescription_id=str(prescription.id),
            prescription_number=prescription.prescription_number,
            patient_ref=prescription.patient.patient_id,
            patient_name=f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
            doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            prescription_date=prescription.prescription_date,
            diagnosis=prescription.diagnosis,
            total_medicines=len(prescription.medications),
            is_dispensed=prescription.is_dispensed,
            created_at=prescription.created_at.isoformat()
        ))
    
    return results


# ============================================================================
# PHARMACIST ENDPOINTS - View Prescriptions
# ============================================================================

@router.get("/pharmacist/prescriptions", response_model=dict)
async def get_prescriptions_for_pharmacist(
    patient_ref: Optional[str] = Query(None, description="Filter by patient reference"),
    is_dispensed: Optional[bool] = Query(None, description="Filter: true=completed only, false=pending only, omit=all"),
    date_from: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    date_to: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Single endpoint for pharmacist: all doctor-generated prescriptions (pending + completed).
    
    Access Control:
    - **Who can access:** Pharmacists, Hospital Admin, Receptionists
    
    Returns:
    - **pending**: prescriptions not yet dispensed (ready to dispense)
    - **completed**: prescriptions already dispensed (purchase completed)
    Use is_dispensed query to filter (true=completed only, false=pending only); omit to get both.
    """
    user_context = get_user_context(current_user)
    
    if user_context["role"] not in [UserRole.PHARMACIST, UserRole.HOSPITAL_ADMIN, UserRole.RECEPTIONIST]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Pharmacist or Receptionist role required"
        )
    
    conditions = [Prescription.hospital_id == user_context["hospital_id"]]
    
    if patient_ref:
        patient_result = await db.execute(
            select(PatientProfile.id)
            .where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == user_context["hospital_id"]
                )
            )
        )
        patient_id = patient_result.scalar_one_or_none()
        if patient_id:
            conditions.append(Prescription.patient_id == patient_id)
    
    if is_dispensed is not None:
        conditions.append(Prescription.is_dispensed == is_dispensed)
    
    if date_from:
        conditions.append(Prescription.prescription_date >= date_from)
    
    if date_to:
        conditions.append(Prescription.prescription_date <= date_to)
    
    prescriptions_result = await db.execute(
        select(Prescription)
        .where(and_(*conditions))
        .options(
            selectinload(Prescription.patient).selectinload(PatientProfile.user),
            selectinload(Prescription.doctor).selectinload(DoctorProfile.user)
        )
        .order_by(desc(Prescription.created_at))
        .limit(limit)
    )
    prescriptions = prescriptions_result.scalars().all()
    hospital_uuid = uuid.UUID(user_context["hospital_id"])

    # For dispensed prescriptions, load batch_ids from StockLedger (fallback when not in medications)
    dispensed_ids = [rx.id for rx in prescriptions if rx.is_dispensed]
    ledger_batches_by_rx = {}  # prescription_id -> { medicine_id_str: [batch_id_str, ...] }
    if dispensed_ids:
        ledger_result = await db.execute(
            select(StockLedger.medicine_id, StockLedger.batch_id, StockLedger.reference_id)
            .where(
                and_(
                    StockLedger.hospital_id == hospital_uuid,
                    StockLedger.reference_type == "PRESCRIPTION",
                    StockLedger.reference_id.in_(dispensed_ids),
                    StockLedger.batch_id.isnot(None),
                )
            )
        )
        for row in ledger_result.all():
            rx_id, med_id, batch_id = row.reference_id, str(row.medicine_id), str(row.batch_id)
            ledger_batches_by_rx.setdefault(rx_id, {}).setdefault(med_id, []).append(batch_id)

    def _enrich_medicines(medications, rx_id=None):
        """Add batch_id and batch_ids from batch_allocations or from ledger fallback."""
        if not medications:
            return []
        ledger_map = (ledger_batches_by_rx.get(rx_id) or {}) if rx_id else {}
        out = []
        for m in medications:
            entry = dict(m)
            batch_allocations = entry.get("batch_allocations") or []
            batch_ids = [a.get("batch_id") for a in batch_allocations if a.get("batch_id")]
            if not batch_ids:
                med_id_raw = entry.get("medicine_id")
                med_id_str = str(med_id_raw) if med_id_raw is not None else None
                if med_id_str and ledger_map:
                    batch_ids = list(dict.fromkeys(ledger_map.get(med_id_str) or []))
            entry["batch_id"] = batch_ids[0] if batch_ids else None
            entry["batch_ids"] = batch_ids
            out.append(entry)
        return out

    def _item(rx):
        return {
            "prescription_id": str(rx.id),
            "prescription_number": rx.prescription_number,
            "patient_ref": rx.patient.patient_id,
            "patient_name": f"{rx.patient.user.first_name} {rx.patient.user.last_name}",
            "doctor_name": f"Dr. {rx.doctor.user.first_name} {rx.doctor.user.last_name}",
            "prescription_date": rx.prescription_date,
            "diagnosis": rx.diagnosis,
            "symptoms": rx.symptoms,
            "medicines": _enrich_medicines(rx.medications or [], rx.id),
            "general_instructions": rx.general_instructions,
            "diet_instructions": rx.diet_instructions,
            "follow_up_date": rx.follow_up_date,
            "is_dispensed": rx.is_dispensed,
            "status": "COMPLETED" if rx.is_dispensed else "PENDING",
            "dispensed_at": rx.dispensed_at,
            "created_at": rx.created_at.isoformat(),
        }
    
    pending = [ _item(rx) for rx in prescriptions if not rx.is_dispensed ]
    completed = [ _item(rx) for rx in prescriptions if rx.is_dispensed ]
    
    return {
        "pending": pending,
        "completed": completed,
        "total_pending": len(pending),
        "total_completed": len(completed),
    }


# ============================================================================
# PHARMACIST ENDPOINTS - Dispense Prescription (Iteration 3: pack + stock ledger)
# ============================================================================

async def _allocate_and_deduct_stock(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    medicine_id: uuid.UUID,
    requested_qty: int,
    prescription_id: uuid.UUID,
    performed_by: uuid.UUID,
) -> List[dict]:
    """
    Select batches FIFO (earliest expiry), lock rows, deduct qty_on_hand, write ledger.
    Returns list of { "batch_id": str, "packed_qty": int }.
    Raises HTTPException if insufficient stock.
    """
    today = date.today()
    # Lock batches for this medicine (FIFO by expiry)
    batches_stmt = (
        select(StockBatch)
        .where(
            and_(
                StockBatch.hospital_id == hospital_id,
                StockBatch.medicine_id == medicine_id,
                StockBatch.expiry_date > today,
                StockBatch.qty_on_hand > 0,
            )
        )
        .order_by(StockBatch.expiry_date.asc())
        .with_for_update()
    )
    result = await db.execute(batches_stmt)
    batches = result.scalars().all()
    if not batches:
        return []
    # Available per batch = qty_on_hand - qty_reserved
    remaining = requested_qty
    allocations = []
    for batch in batches:
        if remaining <= 0:
            break
        available = float(batch.qty_on_hand - (batch.qty_reserved or 0))
        if available <= 0:
            continue
        take = min(int(available), remaining)
        if take <= 0:
            continue
        # Deduct
        batch.qty_on_hand = batch.qty_on_hand - Decimal(str(take))
        # Ledger (negative = out)
        unit_cost = batch.purchase_rate or Decimal("0")
        ledger = StockLedger(
            hospital_id=hospital_id,
            medicine_id=medicine_id,
            batch_id=batch.id,
            txn_type="PRESCRIPTION_DISPENSE",
            qty_change=Decimal(-take),
            unit_cost=unit_cost,
            reference_type="PRESCRIPTION",
            reference_id=prescription_id,
            performed_by=performed_by,
            reason=f"Prescription dispense",
        )
        db.add(ledger)
        allocations.append({"batch_id": str(batch.id), "packed_qty": take})
        remaining -= take
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient stock for medicine {medicine_id}. Short by {remaining} units."
        )
    return allocations


@router.post("/pharmacist/prescriptions/{prescription_id}/dispense")
async def dispense_prescription(
    prescription_id: str,
    background_tasks: BackgroundTasks,
    notes: Optional[str] = Body(None, description="Dispensing notes"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Pack and dispense prescription: FIFO batch selection, stock deduction, ledger.
    
    Access Control:
    - **Who can access:** Pharmacists, Hospital Admin only
    """
    user_context = get_user_context(current_user)
    if user_context["role"] not in [UserRole.PHARMACIST, UserRole.HOSPITAL_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Pharmacist role required"
        )
    hospital_uuid = uuid.UUID(user_context["hospital_id"])
    user_uuid = uuid.UUID(user_context["user_id"])
    rx_id = uuid.UUID(prescription_id)

    # Lock prescription row
    rx_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.id == rx_id,
                Prescription.hospital_id == hospital_uuid,
            )
        )
        .options(
            selectinload(Prescription.patient).selectinload(PatientProfile.user),
            selectinload(Prescription.doctor).selectinload(DoctorProfile.user),
        )
        .with_for_update()
    )
    prescription = rx_result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prescription not found")
    if prescription.is_dispensed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prescription already dispensed"
        )

    medications = prescription.medications or []
    updated_medications = []
    for item in medications:
        med_id_str = item.get("medicine_id")
        qty = item.get("quantity") or item.get("requested_qty") or 0
        if not med_id_str or qty <= 0:
            updated_medications.append(dict(item))
            continue
        try:
            med_id = uuid.UUID(med_id_str)
        except (ValueError, TypeError):
            updated_medications.append(dict(item))
            continue
        allocations = await _allocate_and_deduct_stock(
            db, hospital_uuid, med_id, int(qty), prescription.id, user_uuid
        )
        new_item = dict(item)
        new_item["batch_allocations"] = allocations
        updated_medications.append(new_item)

    prescription.medications = updated_medications
    prescription.is_dispensed = True
    prescription.dispensed_at = datetime.now().isoformat()
    prescription.dispensed_by = user_uuid

    await db.commit()
    await db.refresh(prescription)

    # Notify Patient (and Receptionist) that prescription is ready (event-driven; do not block response)
    background_tasks.add_task(
        _notify_prescription_dispensed_task,
        str(prescription.id),
        user_context["hospital_id"],
    )

    return {
        "message": "Prescription dispensed successfully",
        "prescription_id": str(prescription.id),
        "prescription_number": prescription.prescription_number,
        "patient_name": f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
        "doctor_name": f"Dr. {prescription.doctor.user.first_name} {prescription.doctor.user.last_name}",
        "dispensed_at": prescription.dispensed_at,
        "dispensed_by": user_context["user_id"],
        "notes": notes,
        "batch_allocations_applied": True,
    }


# ============================================================================
# PATIENT ENDPOINTS - List my prescriptions (for PDF download)
# ============================================================================

@router.get("/patient/prescriptions", response_model=List[PrescriptionResponse])
async def get_patient_prescriptions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List prescriptions for the current patient.
    
    Access Control:
    - **Who can access:** Patients only (own prescriptions from JWT token)
    """
    user_context = get_user_context(current_user)
    if user_context["role"] != UserRole.PATIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Patient only",
        )
    if not user_context.get("hospital_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital context required",
        )
    hospital_uuid = uuid.UUID(user_context["hospital_id"])
    patient_result = await db.execute(
        select(PatientProfile).where(
            and_(
                PatientProfile.user_id == user_context["user_id"],
                PatientProfile.hospital_id == hospital_uuid,
            )
        )
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        return []
    prescriptions_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.hospital_id == hospital_uuid,
                Prescription.patient_id == patient.id,
            )
        )
        .options(
            selectinload(Prescription.patient).selectinload(PatientProfile.user),
            selectinload(Prescription.doctor).selectinload(DoctorProfile.user),
        )
        .order_by(desc(Prescription.created_at))
    )
    prescriptions = prescriptions_result.scalars().all()
    return [
        PrescriptionResponse(
            prescription_id=str(p.id),
            prescription_number=p.prescription_number,
            patient_ref=p.patient.patient_id,
            patient_name=f"{p.patient.user.first_name} {p.patient.user.last_name}",
            doctor_name=f"Dr. {p.doctor.user.first_name} {p.doctor.user.last_name}",
            prescription_date=p.prescription_date,
            diagnosis=p.diagnosis,
            total_medicines=len(p.medications or []),
            is_dispensed=p.is_dispensed,
            created_at=p.created_at.isoformat(),
        )
        for p in prescriptions
    ]


# ============================================================================
# COMMON ENDPOINTS - Get Prescription Details
# ============================================================================

@router.get("/prescriptions/{prescription_id}")
async def get_prescription_details(
    prescription_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed prescription information.
    
    Access Control:
    - **Who can access:** Doctors (own), Pharmacists/Receptionists (hospital), Patients (own)
    """
    user_context = get_user_context(current_user)
    
    # Get prescription
    prescription_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.id == uuid.UUID(prescription_id),
                Prescription.hospital_id == user_context["hospital_id"]
            )
        )
        .options(
            selectinload(Prescription.patient).selectinload(PatientProfile.user),
            selectinload(Prescription.doctor).selectinload(DoctorProfile.user)
        )
    )
    
    prescription = prescription_result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescription not found"
        )
    
    # Check access: Doctor own; Pharmacist/Admin all; Patient own
    if user_context["role"] == UserRole.DOCTOR:
        if str(prescription.doctor.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - You can only view your own prescriptions"
            )
    elif user_context["role"] == UserRole.PATIENT:
        if str(prescription.patient.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - You can only view your own prescriptions"
            )
    elif user_context["role"] not in [UserRole.PHARMACIST, UserRole.HOSPITAL_ADMIN, UserRole.RECEPTIONIST]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    return {
        "prescription_id": str(prescription.id),
        "prescription_number": prescription.prescription_number,
        "patient_ref": prescription.patient.patient_id,
        "patient_name": f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
        "doctor_name": f"Dr. {prescription.doctor.user.first_name} {prescription.doctor.user.last_name}",
        "prescription_date": prescription.prescription_date,
        "diagnosis": prescription.diagnosis,
        "symptoms": prescription.symptoms,
        "medicines": prescription.medications,
        "general_instructions": prescription.general_instructions,
        "diet_instructions": prescription.diet_instructions,
        "follow_up_date": prescription.follow_up_date,
        "is_dispensed": prescription.is_dispensed,
        "dispensed_at": prescription.dispensed_at,
        "is_digitally_signed": prescription.is_digitally_signed,
        "created_at": prescription.created_at.isoformat()
    }


# ============================================================================
# PDF DOWNLOAD (Iteration 5) - Patient / Doctor / Pharmacist / Receptionist
# ============================================================================

@router.get("/prescriptions/{prescription_id}/pdf")
async def get_prescription_pdf(
    prescription_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Download prescription as PDF.
    
    Access Control:
    - **Who can access:** Doctors (own), Pharmacists/Receptionists/Admin (hospital), Patients (own)
    """
    user_context = get_user_context(current_user)
    if not user_context.get("hospital_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital context required"
        )
    hospital_uuid = uuid.UUID(user_context["hospital_id"])
    rx_id = uuid.UUID(prescription_id)

    prescription_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.id == rx_id,
                Prescription.hospital_id == hospital_uuid,
            )
        )
        .options(
            selectinload(Prescription.patient).selectinload(PatientProfile.user),
            selectinload(Prescription.doctor).selectinload(DoctorProfile.user),
        )
    )
    prescription = prescription_result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prescription not found")

    # RBAC: Patient own only; Doctor own; Pharmacist / Receptionist / Admin hospital
    role = user_context["role"]
    if role == UserRole.PATIENT:
        if str(prescription.patient.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - You can only download your own prescriptions"
            )
    elif role == UserRole.DOCTOR:
        if str(prescription.doctor.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - You can only download your own prescriptions"
            )
    elif role not in [UserRole.PHARMACIST, UserRole.HOSPITAL_ADMIN, UserRole.RECEPTIONIST]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Load hospital for header
    hospital_result = await db.execute(
        select(Hospital).where(Hospital.id == prescription.hospital_id)
    )
    hospital = hospital_result.scalar_one_or_none()
    hospital_dict = {
        "name": hospital.name if hospital else "Hospital",
        "address": hospital.address if hospital else "",
        "city": hospital.city if hospital else "",
        "state": hospital.state if hospital else "",
        "pincode": hospital.pincode if hospital else "",
        "phone": hospital.phone if hospital else "",
        "email": hospital.email if hospital else "",
    }

    patient_name = f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}"
    doctor_name = f"Dr. {prescription.doctor.user.first_name} {prescription.doctor.user.last_name}"

    pdf_bytes = generate_prescription_pdf(
        hospital=hospital_dict,
        doctor_name=doctor_name,
        patient_name=patient_name,
        patient_ref=prescription.patient.patient_id,
        prescription_number=prescription.prescription_number,
        prescription_id=str(prescription.id),
        prescription_date=prescription.prescription_date or "",
        diagnosis=prescription.diagnosis,
        medications=prescription.medications or [],
        general_instructions=prescription.general_instructions,
        diet_instructions=prescription.diet_instructions,
        follow_up_date=prescription.follow_up_date,
    )

    filename = f"prescription_{prescription.prescription_number or prescription_id}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
