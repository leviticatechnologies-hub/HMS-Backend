"""
Hospital Admin service for hospital-level administrative operations.
Handles department management, staff management, and hospital operations.
CRITICAL: All operations are scoped to the hospital_id from JWT token.
"""
import uuid
import random
from decimal import Decimal
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc, asc, or_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.user import User, Role
from app.models.hospital import Department, Ward
from app.models.tenant import Hospital
from app.models.patient import PatientProfile
from app.models.doctor import DoctorProfile
from app.core.enums import UserRole, UserStatus
from app.core.security import SecurityManager


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    """Parse YYYY-MM-DD from query params / ISO strings."""
    if not value:
        return None
    s = str(value).strip()
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _appointment_calendar_day(value: Any) -> Optional[date]:
    """Normalize appointment_date from ORM (str, date, or datetime) for Python-side filters."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if len(s) >= 10:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _appointment_is_emergency(appointment: Any) -> bool:
    """Appointment model uses appointment_type 'EMERGENCY'; optional is_emergency if added later."""
    if getattr(appointment, "is_emergency", False):
        return True
    return (getattr(appointment, "appointment_type", None) or "").strip().upper() == "EMERGENCY"

def _age_from_date_of_birth(dob: Any) -> Optional[int]:
    """Compute age in years from DOB (date/datetime/ISO string). Returns None when unknown/unparseable."""
    if not dob:
        return None
    d: Optional[date] = None
    if isinstance(dob, datetime):
        d = dob.date()
    elif isinstance(dob, date):
        d = dob
    else:
        s = str(dob).strip()
        if len(s) >= 10:
            try:
                d = date.fromisoformat(s[:10])
            except ValueError:
                d = None
    if not d:
        return None
    today = datetime.utcnow().date()
    age = today.year - d.year - (1 if (today.month, today.day) < (d.month, d.day) else 0)
    return age if age >= 0 else None


def _shift_type_from_timing(shift_timing: Optional[str]) -> str:
    """Map free-text shift label to nurse/receptionist shift_type (DAY, NIGHT, ROTATING)."""
    if not shift_timing:
        return "DAY"
    s = str(shift_timing).lower()
    if "rotat" in s:
        return "ROTATING"
    if "night" in s or "evening" in s:
        return "NIGHT"
    return "DAY"


def _parse_joining_date_iso(raw: Optional[str]) -> Optional[str]:
    """Return YYYY-MM-DD or None. Accepts DD-MM-YYYY and YYYY-MM-DD."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10] if len(s) >= 10 else s, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        if val is None:
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_decimal(val: Any) -> Decimal:
    try:
        if val is None:
            return Decimal("0")
        return Decimal(str(val))
    except Exception:
        return Decimal("0")


# Role.name in DB is VARCHAR; SQLAlchemy must compare to string literals, not Enum instances (avoids DB errors).
_STAFF_ROLE_VALUES_ORDERED: List[str] = [
    UserRole.DOCTOR.value,
    UserRole.NURSE.value,
    UserRole.RECEPTIONIST.value,
    UserRole.LAB_TECH.value,
    UserRole.PHARMACIST.value,
]


class HospitalAdminService:
    """Service class for Hospital Admin operations"""
    
    def __init__(self, db: AsyncSession, hospital_id: uuid.UUID):
        self.db = db
        self.hospital_id = hospital_id
        self.security = SecurityManager()
    
    # ============================================================================
    # TASK 2.1 - DEPARTMENT MANAGEMENT
    # ============================================================================
    
    async def create_department(self, department_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new department within the hospital"""
        from sqlalchemy import func

        def _normalize_list(v):
            if v is None:
                return None
            if isinstance(v, list):
                out = []
                for item in v:
                    if item is None:
                        continue
                    s = str(item).strip()
                    if not s or s == "-":
                        continue
                    out.append(s)
                return out
            if isinstance(v, str):
                s = v.strip()
                if not s or s == "-":
                    return []
                parts = []
                for chunk in s.replace("\r", "\n").split("\n"):
                    for p in chunk.split(","):
                        p = p.strip()
                        if p and p != "-":
                            parts.append(p)
                return parts
            s = str(v).strip()
            return [] if not s or s == "-" else [s]

        # Persist UI-only fields inside settings (Department table doesn't have columns for these yet).
        settings = dict(department_data.get("settings") or {})
        if department_data.get("specializations") is not None:
            settings["specializations"] = _normalize_list(department_data.get("specializations")) or []
        if department_data.get("equipment_list") is not None:
            settings["equipment_list"] = _normalize_list(department_data.get("equipment_list")) or []

        # Check if department code already exists in this hospital
        existing_dept = await self.db.execute(
            select(Department).where(
                and_(
                    Department.hospital_id == self.hospital_id,
                    func.lower(Department.code) == str(department_data["code"]).strip().lower(),
                )
            ).limit(1)
        )
        if existing_dept.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "DEPARTMENT_CODE_EXISTS", "message": "Department with this code already exists"}
            )
        
        # Validate head doctor if provided
        head_doctor_id = None
        head_doctor_name = department_data.get('head_of_department')
        if head_doctor_name:
            head_doctor = await self._get_hospital_doctor_by_name(head_doctor_name)
            if not head_doctor:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "DOCTOR_NOT_FOUND", "message": f"Doctor '{head_doctor_name}' not found in this hospital"}
                )
            head_doctor_id = head_doctor.id
        
        # Create department
        department = Department(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            name=department_data['name'],
            code=str(department_data['code']).strip().upper(),
            description=department_data.get('description'),
            head_doctor_id=head_doctor_id,
            location=department_data.get('location'),
            phone=department_data.get('phone'),
            email=department_data.get('email'),
            is_emergency=department_data.get('emergency_services', False),
            is_icu=department_data.get('is_icu', False),
            bed_capacity=department_data.get('bed_capacity', 0),
            is_24x7=department_data.get('is_24x7', False),
            settings=settings,
        )
        
        self.db.add(department)
        await self.db.commit()
        
        return {
            "department_id": str(department.id),
            "name": department.name,
            "code": department.code,
            "message": "Department created successfully"
        }
    
    async def get_departments(
        self, 
        page: int = 1, 
        limit: int = 50,
        active_only: bool = False
    ) -> Dict[str, Any]:
        """Get paginated list of departments for this hospital"""
        offset = (page - 1) * limit
        
        # Build query with hospital filter
        query = select(Department).options(
            selectinload(Department.head_doctor)
        ).where(Department.hospital_id == self.hospital_id)
        
        if active_only:
            query = query.where(Department.is_active == True)
        
        # Get total count
        count_query = select(func.count(Department.id)).where(Department.hospital_id == self.hospital_id)
        if active_only:
            count_query = count_query.where(Department.is_active == True)
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(Department.name.asc())
        result = await self.db.execute(query)
        departments = result.scalars().all()
        
        # Format response
        department_list = []
        for dept in departments:
            head_doctor_name = None
            if dept.head_doctor:
                head_doctor_name = f"{dept.head_doctor.first_name} {dept.head_doctor.last_name}"

            settings = dept.settings or {}

            operating_hours = None
            if getattr(dept, "is_24x7", False):
                operating_hours = "24x7"
            else:
                opening = getattr(dept, "opening_time", None)
                closing = getattr(dept, "closing_time", None)
                if opening and closing:
                    operating_hours = f"{opening.strftime('%H:%M')} - {closing.strftime('%H:%M')}"
            
            department_list.append({
                "id": str(dept.id),
                "name": dept.name,
                "code": dept.code,
                "description": dept.description,
                "location": dept.location,
                "phone": dept.phone,
                "email": dept.email,
                "is_emergency": dept.is_emergency,
                "is_icu": dept.is_icu,
                "bed_capacity": dept.bed_capacity,
                "is_24x7": dept.is_24x7,
                "is_active": dept.is_active,
                # Frontend expects one of these keys for the "Head" line.
                # Keep backward compatibility by returning all.
                "head_doctor_name": head_doctor_name,
                "head_of_department": head_doctor_name,
                "head": head_doctor_name,
                # Include details so frontend can render without extra call
                "operating_hours": operating_hours,
                "specializations": list(settings.get("specializations") or []),
                "equipment_list": list(settings.get("equipment_list") or []),
                "emergency_services": bool(getattr(dept, "is_emergency", False)),
                "created_at": dept.created_at.isoformat(),
                "updated_at": dept.updated_at.isoformat()
            })
        
        return {
            "departments": department_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }
    
    async def get_department_details(self, department_id: uuid.UUID) -> Dict[str, Any]:
        """Get detailed department information"""
        # Get department with head doctor details
        query = select(Department).options(
            selectinload(Department.head_doctor)
        ).where(
            and_(
                Department.id == department_id,
                Department.hospital_id == self.hospital_id
            )
        )
        
        result = await self.db.execute(query)
        department = result.scalar_one_or_none()
        
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found"}
            )
        
        # IMPORTANT: This endpoint is bound to response_model=DepartmentDetailsOut.
        # That schema requires fields like staff_count/current_bed_occupancy/etc.
        settings = department.settings or {}

        head_name = None
        if department.head_doctor:
            head_name = f"{department.head_doctor.first_name} {department.head_doctor.last_name}".strip()

        # Operating hours: best-effort formatting from opening/closing/is_24x7.
        operating_hours = None
        if getattr(department, "is_24x7", False):
            operating_hours = "24x7"
        else:
            opening = getattr(department, "opening_time", None)
            closing = getattr(department, "closing_time", None)
            if opening and closing:
                operating_hours = f"{opening.strftime('%H:%M')} - {closing.strftime('%H:%M')}"

        # Staff count: count staff profiles mapped to this department (cheap + stable).
        try:
            from sqlalchemy import func
            from app.models.hospital import StaffProfile

            staff_count_result = await self.db.execute(
                select(func.count(StaffProfile.id)).where(
                    and_(
                        StaffProfile.hospital_id == self.hospital_id,
                        StaffProfile.department_id == department.id,
                        StaffProfile.is_active == True,
                    )
                )
            )
            staff_count = int(staff_count_result.scalar() or 0)
        except Exception:
            staff_count = 0

        # Bed occupancy: not modeled per-department in v1 -> keep 0 but present.
        current_bed_occupancy = int(settings.get("current_bed_occupancy") or 0)

        return {
            "id": str(department.id),
            "name": department.name,
            "description": department.description,
            "head_of_department": head_name,
            "location": department.location,
            "phone": department.phone,
            "email": department.email,
            "operating_hours": operating_hours,
            "bed_capacity": department.bed_capacity,
            "current_bed_occupancy": current_bed_occupancy,
            "staff_count": staff_count,
            "specializations": list(settings.get("specializations") or []),
            "equipment_list": list(settings.get("equipment_list") or []),
            "emergency_services": bool(getattr(department, "is_emergency", False)),
            "is_active": bool(department.is_active),
            "created_at": department.created_at.isoformat(),
            "updated_at": department.updated_at.isoformat(),
            # Extras kept for backward compatibility (ignored by response_model)
            "code": department.code,
        }
    
    async def update_department(self, department_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update department information"""
        from sqlalchemy import func

        def _normalize_list(v):
            if v is None:
                return None
            if isinstance(v, list):
                out = []
                for item in v:
                    if item is None:
                        continue
                    s = str(item).strip()
                    if not s or s == "-":
                        continue
                    out.append(s)
                return out
            if isinstance(v, str):
                s = v.strip()
                if not s or s == "-":
                    return []
                parts = []
                for chunk in s.replace("\r", "\n").split("\n"):
                    for p in chunk.split(","):
                        p = p.strip()
                        if p and p != "-":
                            parts.append(p)
                return parts
            s = str(v).strip()
            return [] if not s or s == "-" else [s]
        # Get department
        result = await self.db.execute(
            select(Department).where(
                and_(
                    Department.id == department_id,
                    Department.hospital_id == self.hospital_id
                )
            )
        )
        department = result.scalar_one_or_none()
        
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found"}
            )
        
        # Check if code is being changed and ensure uniqueness
        if "code" in update_data and update_data["code"] != department.code:
            existing_dept = await self.db.execute(
                select(Department).where(
                    and_(
                        Department.hospital_id == self.hospital_id,
                        func.lower(Department.code) == str(update_data["code"]).strip().lower(),
                        Department.id != department_id
                    )
                )
            )
            if existing_dept.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "DEPARTMENT_CODE_EXISTS", "message": "Department with this code already exists"}
                )
        
        # API uses head_of_department; store to head_doctor_id.
        if "head_of_department" in update_data:
            head_doctor_name = update_data["head_of_department"]
            if head_doctor_name:
                head_doctor = await self._get_hospital_doctor_by_name(head_doctor_name)
                if not head_doctor:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "DOCTOR_NOT_FOUND", "message": f"Doctor '{head_doctor_name}' not found in this hospital"}
                    )
                # Replace the name with the actual ID for database update
                update_data["head_doctor_id"] = head_doctor.id
            else:
                update_data["head_doctor_id"] = None
            # Remove the name field since we're using ID internally
            del update_data["head_of_department"]
        
        # Update fields
        # Merge settings for specializations/equipment_list if provided by UI.
        if "specializations" in update_data or "equipment_list" in update_data or "settings" in update_data:
            settings = dict((department.settings or {}))
            if "settings" in update_data and isinstance(update_data.get("settings"), dict):
                settings.update(update_data["settings"])
            if "specializations" in update_data:
                settings["specializations"] = _normalize_list(update_data.get("specializations")) or []
            if "equipment_list" in update_data:
                settings["equipment_list"] = _normalize_list(update_data.get("equipment_list")) or []
            department.settings = settings
            update_data.pop("settings", None)
            update_data.pop("specializations", None)
            update_data.pop("equipment_list", None)

        for field, value in update_data.items():
            if hasattr(department, field) and value is not None:
                setattr(department, field, value)
        
        department.updated_at = datetime.utcnow()
        await self.db.commit()
        
        return {
            "department_id": str(department.id),
            "message": "Department updated successfully"
        }
    
    async def update_department_status(self, department_id: uuid.UUID, is_active: bool) -> Dict[str, Any]:
        """Enable or disable department"""
        # Get department
        result = await self.db.execute(
            select(Department).where(
                and_(
                    Department.id == department_id,
                    Department.hospital_id == self.hospital_id
                )
            )
        )
        department = result.scalar_one_or_none()
        
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found"}
            )
        
        # Update status
        old_status = department.is_active
        department.is_active = is_active
        department.updated_at = datetime.utcnow()
        
        await self.db.commit()
        
        status_text = "enabled" if is_active else "disabled"
        
        return {
            "department_id": str(department.id),
            "old_status": old_status,
            "new_status": is_active,
            "message": f"Department {status_text} successfully"
        }
    
    # ============================================================================
    # TASK 2.2 - STAFF MANAGEMENT (Doctors, Lab, Pharmacy)
    # ============================================================================
    
    async def create_staff_user(self, staff_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create staff user and role-specific profile (doctor / nurse / receptionist) when department is set."""
        from app.models.user import User, Role
        from app.models.tenant import Hospital
        from app.models.hospital import Department
        from app.models.doctor import DoctorProfile
        from app.models.nurse import NurseProfile
        from app.models.receptionist import ReceptionistProfile
        from app.core.enums import UserRole, UserStatus
        from app.services.super_admin_service import generate_staff_id
        from app.models.user import user_roles
        from sqlalchemy import insert

        role_name = (staff_data.get("role") or "").strip()
        if role_name not in [
            UserRole.DOCTOR,
            UserRole.NURSE,
            UserRole.RECEPTIONIST,
            UserRole.LAB_TECH,
            UserRole.PHARMACIST,
        ]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_ROLE",
                    "message": "Role must be DOCTOR, NURSE, RECEPTIONIST, LAB_TECH, or PHARMACIST",
                },
            )

        hospital_result = await self.db.execute(
            select(Hospital).where(Hospital.id == self.hospital_id)
        )
        hospital = hospital_result.scalar_one_or_none()
        if hospital and hospital.email and "@" in hospital.email:
            hospital_domain = hospital.email.split("@", 1)[1].strip().lower()
            staff_email = (staff_data["email"] or "").strip().lower()
            if "@" in staff_email:
                staff_domain = staff_email.split("@", 1)[1]
                if staff_domain != hospital_domain:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "INVALID_EMAIL_DOMAIN",
                            "message": f"Staff email domain must match hospital domain '{hospital_domain}'",
                        },
                    )

        existing_user = await self.db.execute(
            select(User).where(User.email == staff_data["email"])
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "EMAIL_EXISTS", "message": "User with this email already exists"},
            )

        existing_phone = await self.db.execute(
            select(User).where(User.phone == staff_data["phone"])
        )
        if existing_phone.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PHONE_EXISTS", "message": "User with this phone already exists"},
            )

        primary_phone = (staff_data.get("phone") or "").strip()
        ec = (staff_data.get("emergency_contact") or "").strip()
        if ec and ec.replace(" ", "") != primary_phone.replace(" ", ""):
            existing_ec = await self.db.execute(select(User).where(User.phone == ec))
            if existing_ec.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "PHONE_EXISTS",
                        "message": "Emergency contact phone is already used by another user",
                    },
                )

        role_result = await self.db.execute(select(Role).where(Role.name == role_name))
        role = role_result.scalar_one_or_none()
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ROLE_NOT_FOUND", "message": f"Role {role_name} not found"},
            )

        department_for_create = None
        dept_label = "GENERAL"
        dn = (staff_data.get("department_name") or "").strip()
        if role_name == UserRole.DOCTOR and dn:
            department_for_create = await self._get_department_by_name(dn)
            if not department_for_create:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "DEPARTMENT_NOT_FOUND",
                        "message": f"Department '{dn}' not found in this hospital",
                    },
                )
            dept_label = department_for_create.name
        elif role_name in (UserRole.NURSE, UserRole.RECEPTIONIST):
            if dn:
                department_for_create = await self._get_department_by_name(dn)
                if not department_for_create:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={
                            "code": "DEPARTMENT_NOT_FOUND",
                            "message": f"Department '{dn}' not found in this hospital",
                        },
                    )
                dept_label = department_for_create.name
            else:
                department_for_create = await self._get_first_department()
                if department_for_create:
                    dept_label = department_for_create.name
        elif role_name in (UserRole.LAB_TECH, UserRole.PHARMACIST) and dn:
            department_for_create = await self._get_department_by_name(dn)
            if not department_for_create:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "DEPARTMENT_NOT_FOUND",
                        "message": f"Department '{dn}' not found in this hospital",
                    },
                )
            dept_label = department_for_create.name

        department = department_for_create

        if role_name in (UserRole.NURSE, UserRole.RECEPTIONIST) and not department_for_create:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "NO_DEPARTMENT",
                    "message": (
                        "No department found in this hospital. Create a department first, "
                        "or pass department_name when adding a nurse or receptionist."
                    ),
                },
            )

        joining_iso = _parse_joining_date_iso(staff_data.get("joining_date"))
        shift_type = _shift_type_from_timing(staff_data.get("shift_timing"))
        extra_md = dict(staff_data.get("metadata") or {})
        if ec:
            extra_md["emergency_contact"] = ec
        if staff_data.get("shift_timing"):
            extra_md["shift_timing"] = staff_data["shift_timing"]
        if joining_iso:
            extra_md["joining_date"] = joining_iso
        if staff_data.get("address"):
            extra_md["address"] = staff_data["address"].strip()

        if role_name == UserRole.RECEPTIONIST:
            g = (staff_data.get("gender") or "").strip()
            if g:
                extra_md["gender"] = g
            bg = (staff_data.get("blood_group") or "").strip()
            if bg:
                extra_md["blood_group"] = bg
            rw_meta = (staff_data.get("receptionist_work_area") or "OPD").strip()
            extra_md["receptionist_work_area"] = rw_meta
            rey_meta = 0
            if staff_data.get("receptionist_experience_years") is not None:
                try:
                    rey_meta = max(0, int(staff_data["receptionist_experience_years"]))
                except (TypeError, ValueError):
                    rey_meta = 0
            extra_md["receptionist_experience_years"] = rey_meta
            rdn = (staff_data.get("receptionist_designation") or "Front Desk Receptionist").strip()
            extra_md["receptionist_designation"] = rdn

        if role_name == UserRole.NURSE:
            nd = (staff_data.get("nurse_designation") or "").strip()
            if nd:
                extra_md["nurse_designation"] = nd
            ns = (staff_data.get("nurse_specialization") or "").strip()
            if ns:
                extra_md["nurse_specialization"] = ns
            if staff_data.get("nurse_experience_years") is not None:
                extra_md["nurse_experience_years"] = max(
                    0, _safe_int(staff_data.get("nurse_experience_years"), 0)
                )
        if role_name == UserRole.LAB_TECH:
            for key in ("lab_specialization", "lab_designation"):
                v = (staff_data.get(key) or "").strip()
                if v:
                    extra_md[key] = v
            if staff_data.get("lab_experience_years") is not None:
                extra_md["lab_experience_years"] = max(
                    0, _safe_int(staff_data.get("lab_experience_years"), 0)
                )
        if role_name == UserRole.PHARMACIST:
            for key in ("pharmacist_specialization", "pharmacist_designation"):
                v = (staff_data.get(key) or "").strip()
                if v:
                    extra_md[key] = v
            if staff_data.get("pharmacist_experience_years") is not None:
                extra_md["pharmacist_experience_years"] = max(
                    0, _safe_int(staff_data.get("pharmacist_experience_years"), 0)
                )
        if role_name in (UserRole.LAB_TECH, UserRole.PHARMACIST) and department_for_create:
            extra_md["department_id"] = str(department_for_create.id)
            extra_md["department_name"] = department_for_create.name

        spec = (
            (staff_data.get("doctor_specialization") or "").strip()
            or (staff_data.get("specialization") or "").strip()
            or "General"
        )
        if role_name == UserRole.DOCTOR:
            extra_md["doctor_specialization"] = spec
            extra_md["specialization"] = spec
            ey = staff_data.get("doctor_experience_years")
            if ey is not None:
                try:
                    extra_md["doctor_experience_years"] = max(0, int(ey))
                except (TypeError, ValueError):
                    extra_md["doctor_experience_years"] = 0
            cf = staff_data.get("consultation_fee")
            if cf is not None:
                try:
                    extra_md["consultation_fee"] = float(cf)
                except (TypeError, ValueError):
                    pass
            ct = (staff_data.get("consultation_type") or "").strip()
            if ct:
                extra_md["consultation_type"] = ct
            av = (staff_data.get("availability_time") or "").strip()
            if av:
                extra_md["availability_time"] = av
            dd = (staff_data.get("designation") or "").strip()
            if dd:
                extra_md["designation"] = dd

        password_hash = self.security.hash_password(staff_data["password"])
        recv_profile_photo = None
        if role_name == UserRole.RECEPTIONIST:
            recv_profile_photo = (staff_data.get("receptionist_profile_photo_url") or "").strip() or None

        staff_id = generate_staff_id(
            role=role_name,
            department_name=dept_label,
            first_name=staff_data["first_name"],
            last_name=staff_data["last_name"],
        )
        existing_staff_id = await self.db.execute(select(User).where(User.staff_id == staff_id))
        counter = 1
        original_staff_id = staff_id
        while existing_staff_id.scalar_one_or_none():
            staff_id = original_staff_id[:-2] + f"{counter:02d}"
            existing_staff_id = await self.db.execute(select(User).where(User.staff_id == staff_id))
            counter += 1
            if counter > 99:
                staff_id = original_staff_id[:-2] + f"{random.randint(10, 99)}"
                break

        user = User(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            email=staff_data["email"],
            phone=staff_data["phone"],
            password_hash=password_hash,
            first_name=staff_data["first_name"],
            last_name=staff_data["last_name"],
            middle_name=staff_data.get("middle_name"),
            staff_id=staff_id,
            status=UserStatus.ACTIVE,
            email_verified=False,
            phone_verified=False,
            avatar_url=recv_profile_photo,
            user_metadata=extra_md,
        )
        self.db.add(user)
        await self.db.flush()

        await self.db.execute(
            insert(user_roles).values(user_id=user.id, role_id=role.id)
        )

        profiles_created: list[str] = []

        if role_name == UserRole.DOCTOR and department_for_create:
            from app.models.hospital import StaffDepartmentAssignment, StaffProfile
            from app.core.utils import parse_date_string

            effective_from = parse_date_string(staff_data.get("joining_date")) or datetime.utcnow()
            join_date_str = joining_iso or effective_from.date().isoformat()

            has_doc = await self.db.execute(
                select(DoctorProfile.id).where(
                    and_(
                        DoctorProfile.user_id == user.id,
                        DoctorProfile.hospital_id == self.hospital_id,
                    )
                )
            )
            if not has_doc.scalar_one_or_none():
                exp_years = int(extra_md.get("doctor_experience_years", 0) or 0)
                cf_raw = extra_md.get("consultation_fee", 0)
                try:
                    cfee = Decimal(str(cf_raw if cf_raw is not None else 0))
                except Exception:
                    cfee = Decimal("0")
                ct = extra_md.get("consultation_type")
                avt = extra_md.get("availability_time")

                sp = StaffProfile(
                    id=uuid.uuid4(),
                    hospital_id=self.hospital_id,
                    user_id=user.id,
                    department_id=department_for_create.id,
                    employee_id=user.staff_id or user.email,
                    designation="Doctor",
                    joining_date=join_date_str,
                    qualification=None,
                    experience_years=exp_years,
                    specialization=spec,
                    emergency_contact_name=None,
                    emergency_contact_phone=None,
                    emergency_contact_relation=None,
                    is_full_time=True,
                    salary=None,
                    skills=[],
                    certifications=[],
                )
                self.db.add(sp)

                assignment = StaffDepartmentAssignment(
                    id=uuid.uuid4(),
                    hospital_id=self.hospital_id,
                    staff_id=user.id,
                    department_id=department_for_create.id,
                    is_primary=True,
                    effective_from=effective_from,
                    notes=None,
                    is_active=True,
                )
                self.db.add(assignment)

                doc_ref = user.staff_id or f"DOC{str(uuid.uuid4())[:8].upper()}"
                lic = f"AUTO-ML-{self.hospital_id.hex[:8]}-{uuid.uuid4().hex[:10]}".upper()
                doc_designation = (
                    (staff_data.get("designation") or "").strip() or "Staff Physician"
                )
                self.db.add(
                    DoctorProfile(
                        id=uuid.uuid4(),
                        hospital_id=self.hospital_id,
                        user_id=user.id,
                        department_id=department_for_create.id,
                        doctor_id=doc_ref,
                        medical_license_number=lic,
                        designation=doc_designation,
                        specialization=spec,
                        sub_specialization=None,
                        experience_years=exp_years,
                        qualifications=[],
                        certifications=[],
                        medical_associations=[],
                        consultation_fee=cfee,
                        follow_up_fee=None,
                        consultation_type=ct,
                        availability_time=avt,
                        is_available_for_emergency=False,
                        is_accepting_new_patients=True,
                        bio=None,
                        languages_spoken=["English"],
                    )
                )
                profiles_created.append("doctor_profile")
                profiles_created.append("staff_profile")
                profiles_created.append("department_assignment")

                extra_md["department_id"] = str(department_for_create.id)
                extra_md["department_name"] = department_for_create.name
                user.user_metadata = extra_md
        elif role_name == UserRole.DOCTOR:
            user.user_metadata = extra_md

        if role_name == UserRole.NURSE and department:
            has_nurse = await self.db.execute(
                select(NurseProfile.id).where(
                    and_(
                        NurseProfile.user_id == user.id,
                        NurseProfile.hospital_id == self.hospital_id,
                    )
                )
            )
            if not has_nurse.scalar_one_or_none():
                nid = user.staff_id or f"NUR{str(uuid.uuid4())[:8].upper()}"
                nlic = f"AUTO-NL-{uuid.uuid4().hex[:12]}".upper()
                n_des = (extra_md.get("nurse_designation") or "Staff Nurse").strip()
                n_spec = (extra_md.get("nurse_specialization") or "").strip() or None
                ney_i = max(0, _safe_int(extra_md.get("nurse_experience_years"), 0))
                self.db.add(
                    NurseProfile(
                        id=uuid.uuid4(),
                        hospital_id=self.hospital_id,
                        user_id=user.id,
                        department_id=department.id,
                        nurse_id=nid,
                        nursing_license_number=nlic,
                        designation=n_des,
                        specialization=n_spec,
                        experience_years=ney_i,
                        shift_type=shift_type,
                    )
                )
                profiles_created.append("nurse_profile")

        if role_name == UserRole.RECEPTIONIST and department:
            has_rc = await self.db.execute(
                select(ReceptionistProfile.id).where(
                    and_(
                        ReceptionistProfile.user_id == user.id,
                        ReceptionistProfile.hospital_id == self.hospital_id,
                    )
                )
            )
            if not has_rc.scalar_one_or_none():
                rid = user.staff_id or f"RC{str(uuid.uuid4())[:8].upper()}"
                eid = f"EMP-{uuid.uuid4().hex[:12].upper()}"
                rw_area = (extra_md.get("receptionist_work_area") or "OPD").strip()
                try:
                    rey_i = max(0, int(extra_md.get("receptionist_experience_years", 0) or 0))
                except (TypeError, ValueError):
                    rey_i = 0
                rdes = (extra_md.get("receptionist_designation") or "Front Desk Receptionist").strip()
                self.db.add(
                    ReceptionistProfile(
                        id=uuid.uuid4(),
                        hospital_id=self.hospital_id,
                        user_id=user.id,
                        department_id=department.id,
                        receptionist_id=rid,
                        employee_id=eid,
                        designation=rdes,
                        work_area=rw_area,
                        experience_years=rey_i,
                        shift_type=shift_type,
                    )
                )
                profiles_created.append("receptionist_profile")
                extra_md["department_id"] = str(department.id)
                extra_md["department_name"] = department.name
                user.user_metadata = extra_md

        resp_user_id = str(user.id)
        resp_staff_id = user.staff_id
        resp_email = user.email
        resp_first = user.first_name
        resp_last = user.last_name
        await self.db.commit()

        staff_name = f"{resp_first} {resp_last}"
        if role_name == UserRole.DOCTOR:
            staff_name = f"Dr. {staff_name}"
        elif role_name == UserRole.NURSE:
            staff_name = f"Nurse {staff_name}"

        out: Dict[str, Any] = {
            "user_id": resp_user_id,
            "staff_id": resp_staff_id,
            "staff_name": staff_name,
            "email": resp_email,
            "role": role_name,
            "joining_date": joining_iso,
            "profiles_created": profiles_created,
            "message": f"{role_name.replace('_', ' ').title()} created successfully",
        }
        if role_name == UserRole.DOCTOR:
            out["doctor_details"] = {
                "department_name": (extra_md.get("department_name")),
                "doctor_experience_years": extra_md.get("doctor_experience_years"),
                "consultation_fee": extra_md.get("consultation_fee"),
                "consultation_type": extra_md.get("consultation_type"),
                "availability_time": extra_md.get("availability_time"),
                "specialization": spec,
            }
        elif role_name == UserRole.RECEPTIONIST:
            out["receptionist_details"] = {
                "department_name": extra_md.get("department_name"),
                "department_id": extra_md.get("department_id"),
                "work_area": extra_md.get("receptionist_work_area"),
                "experience_years": extra_md.get("receptionist_experience_years"),
                "designation": extra_md.get("receptionist_designation"),
                "gender": extra_md.get("gender"),
                "blood_group": extra_md.get("blood_group"),
                "shift_timing": extra_md.get("shift_timing"),
                "address": extra_md.get("address"),
                "profile_photo_url": recv_profile_photo,
            }
        return out
    
    async def get_staff_users(
        self, 
        page: int = 1, 
        limit: int = 50,
        role_filter: Optional[str] = None,
        active_only: bool = False
    ) -> Dict[str, Any]:
        """Get paginated list of staff users"""
        from app.models.user import User, Role
        
        offset = (page - 1) * limit

        # Build query with hospital filter
        query = select(User).options(
            selectinload(User.roles)
        ).where(User.hospital_id == self.hospital_id)
        
        # Filter by role if specified
        if role_filter:
            query = query.join(User.roles).where(Role.name == role_filter)
        else:
            query = query.join(User.roles).where(Role.name.in_(_STAFF_ROLE_VALUES_ORDERED))
        
        if active_only:
            query = query.where(User.is_active == True)
        
        # Get total count (same role filter as list query)
        count_query = select(func.count(User.id)).where(User.hospital_id == self.hospital_id)
        if role_filter:
            count_query = count_query.join(User.roles).where(Role.name == role_filter)
        else:
            count_query = count_query.join(User.roles).where(Role.name.in_(_STAFF_ROLE_VALUES_ORDERED))
        if active_only:
            count_query = count_query.where(User.is_active == True)
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(User.first_name.asc(), User.last_name.asc())
        result = await self.db.execute(query)
        users = result.scalars().all()
        
        # Format response
        staff_list = []
        for user in users:
            user_roles = [role.name for role in user.roles]
            primary_role = next(
                (r for r in _STAFF_ROLE_VALUES_ORDERED if r in user_roles),
                None,
            )
            md = user.user_metadata or {}
            joining = md.get("joining_date")
            specialization = None
            if primary_role == UserRole.DOCTOR.value:
                specialization = (
                    md.get("doctor_specialization")
                    or md.get("specialization")
                    or "General"
                )
            
            # Generate staff name with appropriate title
            staff_name = f"{user.first_name} {user.last_name}"
            if primary_role == UserRole.DOCTOR.value:
                staff_name = f"Dr. {staff_name}"
            elif primary_role == UserRole.NURSE.value:
                staff_name = f"Nurse {staff_name}"
            
            staff_list.append({
                "id": str(user.id),
                "staff_id": user.staff_id,
                "staff_name": staff_name,
                "email": user.email,
                "phone": user.phone,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "middle_name": user.middle_name,
                "primary_role": primary_role,
                "role": primary_role or "",
                "all_roles": user_roles,
                "shift_timing": md.get("shift_timing"),
                "hire_date": joining,
                "joining_date": joining,
                "specialization": specialization,
                "status": user.status,
                "is_active": user.is_active,
                "email_verified": user.email_verified,
                "phone_verified": user.phone_verified,
                "last_login": user.last_login.isoformat() if user.last_login else None,
                "created_at": user.created_at.isoformat(),
                "updated_at": user.updated_at.isoformat()
            })
        
        return {
            "staff": staff_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }
    
    async def get_staff_details(self, staff_id: uuid.UUID) -> Dict[str, Any]:
        """Get detailed staff user information"""
        from app.models.user import User
        
        # Get user with roles
        query = select(User).options(
            selectinload(User.roles)
        ).where(
            and_(
                User.id == staff_id,
                User.hospital_id == self.hospital_id
            )
        )
        
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "STAFF_NOT_FOUND", "message": "Staff user not found"}
            )
        
        user_roles = [role.name for role in user.roles]
        if not any(r in _STAFF_ROLE_VALUES_ORDERED for r in user_roles):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_STAFF_USER", "message": "User is not a staff member"}
            )

        primary_role = next(
            (r for r in _STAFF_ROLE_VALUES_ORDERED if r in user_roles),
            None,
        )
        md = user.user_metadata or {}
        joining = md.get("joining_date")
        shift_timing = md.get("shift_timing")
        dept_raw = md.get("department") or md.get("department_name")
        department_name = (str(dept_raw).strip() if dept_raw else None) or None

        profile_info = {}
        if primary_role == UserRole.DOCTOR.value:
            doctor_result = await self.db.execute(
                select(DoctorProfile)
                .options(selectinload(DoctorProfile.department))
                .where(DoctorProfile.user_id == staff_id)
            )
            doctor_profile = doctor_result.scalar_one_or_none()
            if doctor_profile:
                if getattr(doctor_profile, "department", None) and doctor_profile.department.name:
                    department_name = doctor_profile.department.name
                profile_info = {
                    "doctor_id": doctor_profile.doctor_id,
                    "medical_license_number": doctor_profile.medical_license_number,
                    "designation": doctor_profile.designation,
                    "specialization": doctor_profile.specialization,
                    "experience_years": doctor_profile.experience_years,
                    "consultation_fee": float(doctor_profile.consultation_fee)
                    if doctor_profile.consultation_fee
                    else None,
                }

        role_str = primary_role or ""
        specialization = None
        if primary_role == UserRole.DOCTOR.value:
            specialization = (
                profile_info.get("specialization")
                or md.get("doctor_specialization")
                or md.get("specialization")
                or "General"
            )
        
        return {
            "id": str(user.id),
            "email": user.email,
            "phone": user.phone,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "middle_name": user.middle_name,
            "primary_role": primary_role,
            "role": role_str,
            "all_roles": user_roles,
            "department": department_name,
            "hire_date": joining,
            "shift_timing": shift_timing,
            "address": md.get("address"),
            "emergency_contact": md.get("emergency_contact"),
            "specialization": specialization,
            "status": user.status,
            "is_active": user.is_active,
            "email_verified": user.email_verified,
            "phone_verified": user.phone_verified,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "failed_login_attempts": user.failed_login_attempts,
            "locked_until": user.locked_until.isoformat() if user.locked_until else None,
            "password_changed_at": user.password_changed_at.isoformat() if user.password_changed_at else None,
            "user_metadata": user.user_metadata,
            "profile_info": profile_info,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat()
        }

    async def _get_staff_user_for_role(self, staff_id: uuid.UUID, expected_role: str) -> User:
        """Load a hospital staff user and verify expected role membership."""
        from app.models.user import User

        result = await self.db.execute(
            select(User).options(selectinload(User.roles)).where(
                and_(
                    User.id == staff_id,
                    User.hospital_id == self.hospital_id,
                )
            )
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "STAFF_NOT_FOUND", "message": "Staff user not found"},
            )

        user_roles = [role.name for role in user.roles]
        exp = getattr(expected_role, "value", expected_role)
        if exp not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "ROLE_MISMATCH",
                    "message": f"Staff user is not assigned role {exp}",
                },
            )
        return user

    async def _apply_common_staff_updates(self, user: User, update_data: Dict[str, Any]) -> List[str]:
        """Apply fields shared by all staff roles."""
        updated_fields: List[str] = []

        if "email" in update_data:
            email = str(update_data["email"]).strip().lower()
            if email and email != user.email:
                existing = await self.db.execute(
                    select(User.id).where(
                        and_(
                            User.email == email,
                            User.id != user.id,
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={"code": "EMAIL_EXISTS", "message": "Email already in use"},
                    )
                user.email = email
                updated_fields.append("email")

        if "phone" in update_data:
            phone = str(update_data["phone"]).strip()
            if phone and phone != user.phone:
                existing = await self.db.execute(
                    select(User.id).where(
                        and_(
                            User.phone == phone,
                            User.id != user.id,
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={"code": "PHONE_EXISTS", "message": "Phone already in use"},
                    )
                user.phone = phone
                updated_fields.append("phone")

        for field in ("first_name", "last_name", "middle_name"):
            if field in update_data:
                value = update_data[field]
                if value != getattr(user, field):
                    setattr(user, field, value)
                    updated_fields.append(field)

        md = dict(user.user_metadata or {})
        if "emergency_contact" in update_data:
            md["emergency_contact"] = update_data["emergency_contact"]
            updated_fields.append("emergency_contact")
        if "shift_timing" in update_data:
            md["shift_timing"] = update_data["shift_timing"]
            updated_fields.append("shift_timing")
        if "joining_date" in update_data:
            md["joining_date"] = _parse_joining_date_iso(update_data.get("joining_date"))
            updated_fields.append("joining_date")
        if "address" in update_data:
            md["address"] = update_data["address"]
            updated_fields.append("address")

        user.user_metadata = md
        return updated_fields

    async def update_doctor_staff(self, staff_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update doctor staff profile (hospital admin portal)."""
        user = await self._get_staff_user_for_role(staff_id, UserRole.DOCTOR)
        updated_fields = await self._apply_common_staff_updates(user, update_data)

        md = dict(user.user_metadata or {})
        doctor_result = await self.db.execute(
            select(DoctorProfile).where(
                and_(
                    DoctorProfile.user_id == user.id,
                    DoctorProfile.hospital_id == self.hospital_id,
                )
            )
        )
        doctor_profile = doctor_result.scalar_one_or_none()

        if "department_name" in update_data:
            department = await self._get_department_by_name(update_data["department_name"])
            if not department:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found in this hospital"},
                )
            md["department_id"] = str(department.id)
            md["department_name"] = department.name
            if doctor_profile:
                doctor_profile.department_id = department.id
            updated_fields.append("department_name")

        mapping = {
            "doctor_specialization": "doctor_specialization",
            "doctor_experience_years": "doctor_experience_years",
            "consultation_fee": "consultation_fee",
            "consultation_type": "consultation_type",
            "availability_time": "availability_time",
        }
        for req_key, md_key in mapping.items():
            if req_key in update_data:
                md[md_key] = update_data[req_key]
                updated_fields.append(req_key)

        if doctor_profile:
            if "designation" in update_data:
                doctor_profile.designation = update_data["designation"]
                updated_fields.append("designation")
            if "doctor_specialization" in update_data:
                doctor_profile.specialization = update_data["doctor_specialization"]
            if "doctor_experience_years" in update_data:
                doctor_profile.experience_years = _safe_int(
                    update_data.get("doctor_experience_years"), 0
                )
            if "consultation_fee" in update_data:
                doctor_profile.consultation_fee = _safe_decimal(update_data.get("consultation_fee"))
            if "consultation_type" in update_data:
                doctor_profile.consultation_type = update_data["consultation_type"]
            if "availability_time" in update_data:
                doctor_profile.availability_time = update_data["availability_time"]
            doctor_profile.updated_at = datetime.utcnow()

        user.user_metadata = md
        user.updated_at = datetime.utcnow()
        await self.db.commit()
        return {
            "user_id": str(user.id),
            "role": UserRole.DOCTOR.value,
            "updated_fields": sorted(set(updated_fields)),
            "message": "Doctor staff updated successfully",
        }

    async def update_nurse_staff(self, staff_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update nurse staff profile (hospital admin portal)."""
        from app.models.nurse import NurseProfile

        user = await self._get_staff_user_for_role(staff_id, UserRole.NURSE)
        updated_fields = await self._apply_common_staff_updates(user, update_data)

        md = dict(user.user_metadata or {})
        profile_result = await self.db.execute(
            select(NurseProfile).where(
                and_(
                    NurseProfile.user_id == user.id,
                    NurseProfile.hospital_id == self.hospital_id,
                )
            )
        )
        nurse_profile = profile_result.scalar_one_or_none()

        if "department_name" in update_data:
            department = await self._get_department_by_name(update_data["department_name"])
            if not department:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found in this hospital"},
                )
            md["department_id"] = str(department.id)
            md["department_name"] = department.name
            if nurse_profile:
                nurse_profile.department_id = department.id
            updated_fields.append("department_name")

        if "nurse_specialization" in update_data:
            md["nurse_specialization"] = update_data["nurse_specialization"]
            updated_fields.append("nurse_specialization")
        if "nurse_experience_years" in update_data:
            md["nurse_experience_years"] = update_data["nurse_experience_years"]
            updated_fields.append("nurse_experience_years")

        if nurse_profile:
            if "nurse_designation" in update_data:
                nurse_profile.designation = update_data["nurse_designation"]
                updated_fields.append("nurse_designation")
            if "nurse_specialization" in update_data:
                nurse_profile.specialization = update_data["nurse_specialization"]
            if "nurse_experience_years" in update_data:
                nurse_profile.experience_years = _safe_int(
                    update_data.get("nurse_experience_years"), 0
                )
            if "shift_timing" in update_data:
                nurse_profile.shift_type = _shift_type_from_timing(update_data["shift_timing"])
            nurse_profile.updated_at = datetime.utcnow()

        user.user_metadata = md
        user.updated_at = datetime.utcnow()
        await self.db.commit()
        return {
            "user_id": str(user.id),
            "role": UserRole.NURSE.value,
            "updated_fields": sorted(set(updated_fields)),
            "message": "Nurse staff updated successfully",
        }

    async def update_receptionist_staff(self, staff_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update receptionist staff profile (hospital admin portal)."""
        from app.models.receptionist import ReceptionistProfile

        user = await self._get_staff_user_for_role(staff_id, UserRole.RECEPTIONIST)
        updated_fields = await self._apply_common_staff_updates(user, update_data)

        md = dict(user.user_metadata or {})
        profile_result = await self.db.execute(
            select(ReceptionistProfile).where(
                and_(
                    ReceptionistProfile.user_id == user.id,
                    ReceptionistProfile.hospital_id == self.hospital_id,
                )
            )
        )
        receptionist_profile = profile_result.scalar_one_or_none()

        if "department_name" in update_data:
            department = await self._get_department_by_name(update_data["department_name"])
            if not department:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found in this hospital"},
                )
            md["department_id"] = str(department.id)
            md["department_name"] = department.name
            if receptionist_profile:
                receptionist_profile.department_id = department.id
            updated_fields.append("department_name")

        recv_fields = (
            "receptionist_work_area",
            "receptionist_experience_years",
            "receptionist_designation",
            "gender",
            "blood_group",
        )
        for key in recv_fields:
            if key in update_data:
                md[key] = update_data[key]
                updated_fields.append(key)
        if "receptionist_profile_photo_url" in update_data:
            user.avatar_url = update_data["receptionist_profile_photo_url"]
            updated_fields.append("receptionist_profile_photo_url")

        if receptionist_profile:
            if "receptionist_designation" in update_data:
                receptionist_profile.designation = update_data["receptionist_designation"]
            if "receptionist_work_area" in update_data:
                receptionist_profile.work_area = update_data["receptionist_work_area"]
            if "receptionist_experience_years" in update_data:
                receptionist_profile.experience_years = _safe_int(
                    update_data.get("receptionist_experience_years"), 0
                )
            if "shift_timing" in update_data:
                receptionist_profile.shift_type = _shift_type_from_timing(update_data["shift_timing"])
            receptionist_profile.updated_at = datetime.utcnow()

        user.user_metadata = md
        user.updated_at = datetime.utcnow()
        await self.db.commit()
        return {
            "user_id": str(user.id),
            "role": UserRole.RECEPTIONIST.value,
            "updated_fields": sorted(set(updated_fields)),
            "message": "Receptionist staff updated successfully",
        }

    async def update_lab_tech_staff(self, staff_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update lab tech staff profile (hospital admin portal)."""
        user = await self._get_staff_user_for_role(staff_id, UserRole.LAB_TECH)
        updated_fields = await self._apply_common_staff_updates(user, update_data)

        md = dict(user.user_metadata or {})
        for key in ("lab_specialization", "lab_designation", "lab_experience_years"):
            if key in update_data:
                md[key] = update_data[key]
                updated_fields.append(key)
        if "department_name" in update_data:
            department = await self._get_department_by_name(update_data["department_name"])
            if not department:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found in this hospital"},
                )
            md["department_id"] = str(department.id)
            md["department_name"] = department.name
            updated_fields.append("department_name")

        user.user_metadata = md
        user.updated_at = datetime.utcnow()
        await self.db.commit()
        return {
            "user_id": str(user.id),
            "role": UserRole.LAB_TECH.value,
            "updated_fields": sorted(set(updated_fields)),
            "message": "Lab tech staff updated successfully",
        }

    async def update_pharmacist_staff(self, staff_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update pharmacist staff profile (hospital admin portal)."""
        user = await self._get_staff_user_for_role(staff_id, UserRole.PHARMACIST)
        updated_fields = await self._apply_common_staff_updates(user, update_data)

        md = dict(user.user_metadata or {})
        for key in ("pharmacist_specialization", "pharmacist_designation", "pharmacist_experience_years"):
            if key in update_data:
                md[key] = update_data[key]
                updated_fields.append(key)
        if "department_name" in update_data:
            department = await self._get_department_by_name(update_data["department_name"])
            if not department:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found in this hospital"},
                )
            md["department_id"] = str(department.id)
            md["department_name"] = department.name
            updated_fields.append("department_name")

        user.user_metadata = md
        user.updated_at = datetime.utcnow()
        await self.db.commit()
        return {
            "user_id": str(user.id),
            "role": UserRole.PHARMACIST.value,
            "updated_fields": sorted(set(updated_fields)),
            "message": "Pharmacist staff updated successfully",
        }
    
    async def update_staff_status(self, staff_id: uuid.UUID, is_active: bool) -> Dict[str, Any]:
        """Activate or deactivate staff user"""
        from app.models.user import User
        
        # Get user
        result = await self.db.execute(
            select(User).options(selectinload(User.roles)).where(
                and_(
                    User.id == staff_id,
                    User.hospital_id == self.hospital_id
                )
            )
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "STAFF_NOT_FOUND", "message": "Staff user not found"}
            )
        
        # Check if user has staff role
        user_roles = [role.name for role in user.roles]
        if not any(r in _STAFF_ROLE_VALUES_ORDERED for r in user_roles):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_STAFF_USER", "message": "User is not a staff member"}
            )
        
        # Update status
        old_status = user.is_active
        user.is_active = is_active
        user.updated_at = datetime.utcnow()
        
        # Mirror super-admin convention: deactivated accounts use BLOCKED (no UserStatus.INACTIVE).
        if not is_active:
            user.status = UserStatus.BLOCKED.value
        else:
            user.status = UserStatus.ACTIVE.value
        
        await self.db.commit()
        
        status_text = "activated" if is_active else "deactivated"
        status_str = (
            user.status
            if isinstance(user.status, str)
            else getattr(user.status, "value", str(user.status))
        )
        
        return {
            "user_id": str(user.id),
            "staff_id": user.staff_id,
            "is_active": user.is_active,
            "status": status_str,
            "old_is_active": old_status,
            "new_is_active": is_active,
            "message": f"Staff user {status_text} successfully",
        }
    
    async def reset_staff_password(self, staff_id: uuid.UUID) -> Dict[str, Any]:
        """Reset staff user password"""
        from app.models.user import User
        
        # Get user
        result = await self.db.execute(
            select(User).options(selectinload(User.roles)).where(
                and_(
                    User.id == staff_id,
                    User.hospital_id == self.hospital_id
                )
            )
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "STAFF_NOT_FOUND", "message": "Staff user not found"}
            )
        
        user_roles = [role.name for role in user.roles]
        if not any(r in _STAFF_ROLE_VALUES_ORDERED for r in user_roles):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_STAFF_USER", "message": "User is not a staff member"}
            )
        
        # Generate new temporary password
        temp_password = self.security.generate_temp_password()
        password_hash = self.security.hash_password(temp_password)
        
        # Update password
        user.password_hash = password_hash
        user.password_changed_at = datetime.utcnow()
        user.failed_login_attempts = 0
        user.locked_until = None
        user.updated_at = datetime.utcnow()
        
        await self.db.commit()
        
        return {
            "user_id": str(user.id),
            "temp_password": temp_password,
            "message": "Password reset successfully"
        }

    # ============================================================================
    # TASK 2.3 - DOCTOR PROFILE MANAGEMENT
    # ============================================================================
    
    async def create_doctor_profile(self, doctor_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create doctor profile for existing doctor user"""
        from app.models.doctor import DoctorProfile
        from app.models.user import User
        
        user_id = uuid.UUID(doctor_data['user_id'])
        
        # Verify user exists and is a doctor in this hospital
        user_result = await self.db.execute(
            select(User).options(selectinload(User.roles)).where(
                and_(
                    User.id == user_id,
                    User.hospital_id == self.hospital_id
                )
            )
        )
        user = user_result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "USER_NOT_FOUND", "message": "User not found in this hospital"}
            )
        
        # Check if user has doctor role
        user_roles = [role.name for role in user.roles]
        if UserRole.DOCTOR not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_DOCTOR", "message": "User must have DOCTOR role"}
            )
        
        # Check if doctor profile already exists
        existing_profile = await self.db.execute(
            select(DoctorProfile).where(DoctorProfile.user_id == user_id)
        )
        if existing_profile.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PROFILE_EXISTS", "message": "Doctor profile already exists"}
            )
        
        # Check if doctor_id is unique within hospital
        if 'doctor_id' in doctor_data:
            existing_doctor_id = await self.db.execute(
                select(DoctorProfile).where(
                    and_(
                        DoctorProfile.doctor_id == doctor_data['doctor_id'],
                        DoctorProfile.hospital_id == self.hospital_id
                    )
                )
            )
            if existing_doctor_id.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "DOCTOR_ID_EXISTS", "message": "Doctor ID already exists in this hospital"}
                )
        
        # Check if medical license number is unique
        if 'medical_license_number' in doctor_data:
            existing_license = await self.db.execute(
                select(DoctorProfile).where(
                    DoctorProfile.medical_license_number == doctor_data['medical_license_number']
                )
            )
            if existing_license.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "LICENSE_EXISTS", "message": "Medical license number already exists"}
                )
        
        # Validate department if provided
        department_id = doctor_data.get('department_id')
        if department_id:
            department = await self._get_hospital_department(uuid.UUID(department_id))
            if not department:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found in this hospital"}
                )
        
        # Create doctor profile
        doctor_profile = DoctorProfile(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            user_id=user_id,
            department_id=uuid.UUID(department_id) if department_id else None,
            doctor_id=doctor_data.get('doctor_id', f"DOC{str(uuid.uuid4())[:8].upper()}"),
            medical_license_number=doctor_data['medical_license_number'],
            designation=doctor_data['designation'],
            specialization=doctor_data['specialization'],
            sub_specialization=doctor_data.get('sub_specialization'),
            experience_years=doctor_data.get('experience_years', 0),
            qualifications=doctor_data.get('qualifications', []),
            certifications=doctor_data.get('certifications', []),
            medical_associations=doctor_data.get('medical_associations', []),
            consultation_fee=doctor_data.get('consultation_fee', 0),
            follow_up_fee=doctor_data.get('follow_up_fee'),
            is_available_for_emergency=doctor_data.get('is_available_for_emergency', False),
            is_accepting_new_patients=doctor_data.get('is_accepting_new_patients', True),
            bio=doctor_data.get('bio'),
            languages_spoken=doctor_data.get('languages_spoken', ["English"])
        )
        
        self.db.add(doctor_profile)
        await self.db.commit()
        
        return {
            "doctor_profile_id": str(doctor_profile.id),
            "doctor_id": doctor_profile.doctor_id,
            "user_id": str(user_id),
            "message": "Doctor profile created successfully"
        }
    
    async def get_doctors(
        self, 
        page: int = 1, 
        limit: int = 50,
        department_id: Optional[str] = None,
        specialization: Optional[str] = None,
        active_only: bool = False
    ) -> Dict[str, Any]:
        """Get paginated list of doctors"""
        from app.models.doctor import DoctorProfile
        from app.models.user import User
        from app.models.hospital import Department
        
        offset = (page - 1) * limit
        
        # Build query with hospital filter
        query = select(DoctorProfile).options(
            selectinload(DoctorProfile.user),
            selectinload(DoctorProfile.department)
        ).where(DoctorProfile.hospital_id == self.hospital_id)
        
        # Filter by department
        if department_id:
            query = query.where(DoctorProfile.department_id == uuid.UUID(department_id))
        
        # Filter by specialization
        if specialization:
            query = query.where(DoctorProfile.specialization.ilike(f"%{specialization}%"))
        
        # Filter by active status
        if active_only:
            query = query.join(DoctorProfile.user).where(User.is_active == True)
        
        # Get total count
        count_query = select(func.count(DoctorProfile.id)).where(DoctorProfile.hospital_id == self.hospital_id)
        if department_id:
            count_query = count_query.where(DoctorProfile.department_id == uuid.UUID(department_id))
        if specialization:
            count_query = count_query.where(DoctorProfile.specialization.ilike(f"%{specialization}%"))
        if active_only:
            count_query = count_query.join(DoctorProfile.user).where(User.is_active == True)
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(DoctorProfile.created_at.desc())
        result = await self.db.execute(query)
        doctors = result.scalars().all()
        
        # Format response
        doctor_list = []
        for doctor in doctors:
            doctor_list.append({
                "id": str(doctor.id),
                "doctor_id": doctor.doctor_id,
                "user_id": str(doctor.user_id),
                "user_name": f"{doctor.user.first_name} {doctor.user.last_name}",
                "email": doctor.user.email,
                "phone": doctor.user.phone,
                "department_id": str(doctor.department_id) if doctor.department_id else None,
                "department_name": doctor.department.name if doctor.department else None,
                "designation": doctor.designation,
                "specialization": doctor.specialization,
                "sub_specialization": doctor.sub_specialization,
                "experience_years": doctor.experience_years,
                "consultation_fee": float(doctor.consultation_fee) if doctor.consultation_fee else None,
                "is_available_for_emergency": doctor.is_available_for_emergency,
                "is_accepting_new_patients": doctor.is_accepting_new_patients,
                "is_active": doctor.user.is_active,
                "created_at": doctor.created_at.isoformat(),
                "updated_at": doctor.updated_at.isoformat()
            })
        
        return {
            "doctors": doctor_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }
    
    async def update_doctor_profile(self, doctor_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update doctor profile information"""
        from app.models.doctor import DoctorProfile
        
        # Get doctor profile
        result = await self.db.execute(
            select(DoctorProfile).where(
                and_(
                    DoctorProfile.id == doctor_id,
                    DoctorProfile.hospital_id == self.hospital_id
                )
            )
        )
        doctor = result.scalar_one_or_none()
        
        if not doctor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DOCTOR_NOT_FOUND", "message": "Doctor profile not found"}
            )
        
        # Check if doctor_id is being changed and ensure uniqueness
        if "doctor_id" in update_data and update_data["doctor_id"] != doctor.doctor_id:
            existing_doctor_id = await self.db.execute(
                select(DoctorProfile).where(
                    and_(
                        DoctorProfile.doctor_id == update_data["doctor_id"],
                        DoctorProfile.hospital_id == self.hospital_id,
                        DoctorProfile.id != doctor_id
                    )
                )
            )
            if existing_doctor_id.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "DOCTOR_ID_EXISTS", "message": "Doctor ID already exists in this hospital"}
                )
        
        # Check if medical license is being changed and ensure uniqueness
        if "medical_license_number" in update_data and update_data["medical_license_number"] != doctor.medical_license_number:
            existing_license = await self.db.execute(
                select(DoctorProfile).where(
                    and_(
                        DoctorProfile.medical_license_number == update_data["medical_license_number"],
                        DoctorProfile.id != doctor_id
                    )
                )
            )
            if existing_license.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "LICENSE_EXISTS", "message": "Medical license number already exists"}
                )
        
        # Validate department if being changed
        if "department_id" in update_data:
            department_id = update_data["department_id"]
            if department_id:
                department = await self._get_hospital_department(uuid.UUID(department_id))
                if not department:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "DEPARTMENT_NOT_FOUND", "message": "Department not found in this hospital"}
                    )
                update_data["department_id"] = uuid.UUID(department_id)
            else:
                update_data["department_id"] = None
        
        # Update fields
        for field, value in update_data.items():
            if hasattr(doctor, field) and value is not None:
                setattr(doctor, field, value)
        
        doctor.updated_at = datetime.utcnow()
        await self.db.commit()
        
        return {
            "doctor_profile_id": str(doctor.id),
            "message": "Doctor profile updated successfully"
        }
    
    # ============================================================================
    # TASK 2.3 - DOCTOR SCHEDULE MANAGEMENT
    # ============================================================================
    
    async def create_doctor_schedule(self, doctor_id: uuid.UUID, schedule_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create doctor schedule"""
        from app.models.doctor import DoctorProfile
        from app.models.schedule import DoctorSchedule
        from app.core.utils import parse_time_string
        
        # Verify doctor exists in this hospital
        doctor_result = await self.db.execute(
            select(DoctorProfile).where(
                and_(
                    DoctorProfile.id == doctor_id,
                    DoctorProfile.hospital_id == self.hospital_id
                )
            )
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if not doctor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DOCTOR_NOT_FOUND", "message": "Doctor not found in this hospital"}
            )
        
        # Check for schedule conflicts
        existing_schedule = await self.db.execute(
            select(DoctorSchedule).where(
                and_(
                    DoctorSchedule.doctor_id == doctor_id,
                    DoctorSchedule.day_of_week == schedule_data['day_of_week'],
                    DoctorSchedule.is_active == True
                )
            )
        )
        if existing_schedule.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "SCHEDULE_CONFLICT", "message": f"Schedule already exists for {schedule_data['day_of_week']}"}
            )
        
        # Parse time strings
        start_time = parse_time_string(schedule_data['start_time'])
        end_time = parse_time_string(schedule_data['end_time'])
        
        # Validate time range
        if start_time >= end_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_TIME_RANGE", "message": "Start time must be before end time"}
            )
        
        # Parse break times if provided
        break_start_time = None
        break_end_time = None
        if schedule_data.get('break_start_time') and schedule_data.get('break_end_time'):
            break_start_time = parse_time_string(schedule_data['break_start_time'])
            break_end_time = parse_time_string(schedule_data['break_end_time'])
            
            # Validate break time range
            if break_start_time >= break_end_time:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "INVALID_BREAK_TIME", "message": "Break start time must be before break end time"}
                )
            
            # Validate break times are within working hours
            if break_start_time < start_time or break_end_time > end_time:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "BREAK_OUT_OF_RANGE", "message": "Break times must be within working hours"}
                )
        
        if "slot_duration_minutes" not in schedule_data or schedule_data.get("slot_duration_minutes") is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "SLOT_DURATION_REQUIRED", "message": "slot_duration_minutes is required (15–120)."},
            )
        slot_mins = int(schedule_data["slot_duration_minutes"])
        if slot_mins < 15 or slot_mins > 120:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_SLOT_DURATION", "message": "slot_duration_minutes must be between 15 and 120."},
            )

        # Create schedule
        schedule = DoctorSchedule(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            doctor_id=doctor_id,
            day_of_week=schedule_data['day_of_week'],
            start_time=start_time,
            end_time=end_time,
            slot_duration_minutes=slot_mins,
            break_start_time=break_start_time,
            break_end_time=break_end_time,
            max_patients_per_slot=schedule_data.get('max_patients_per_slot', 1),
            is_emergency_available=schedule_data.get('is_emergency_available', False),
            effective_from=schedule_data.get('effective_from'),
            effective_to=schedule_data.get('effective_to'),
            notes=schedule_data.get('notes')
        )
        
        self.db.add(schedule)
        await self.db.commit()
        
        return {
            "schedule_id": str(schedule.id),
            "doctor_id": str(doctor_id),
            "day_of_week": schedule.day_of_week,
            "message": "Doctor schedule created successfully"
        }
    
    async def get_doctor_schedules(self, doctor_id: uuid.UUID) -> Dict[str, Any]:
        """Get doctor schedules"""
        from app.models.doctor import DoctorProfile
        from app.models.schedule import DoctorSchedule
        
        # Verify doctor exists in this hospital
        doctor_result = await self.db.execute(
            select(DoctorProfile).options(selectinload(DoctorProfile.user)).where(
                and_(
                    DoctorProfile.id == doctor_id,
                    DoctorProfile.hospital_id == self.hospital_id
                )
            )
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if not doctor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DOCTOR_NOT_FOUND", "message": "Doctor not found in this hospital"}
            )
        
        # Get schedules
        schedules_result = await self.db.execute(
            select(DoctorSchedule).where(
                DoctorSchedule.doctor_id == doctor_id
            ).order_by(
                DoctorSchedule.day_of_week.asc(),
                DoctorSchedule.start_time.asc()
            )
        )
        schedules = schedules_result.scalars().all()
        
        # Format response
        schedule_list = []
        for schedule in schedules:
            schedule_list.append({
                "id": str(schedule.id),
                "day_of_week": schedule.day_of_week,
                "start_time": schedule.start_time.strftime("%H:%M") if schedule.start_time else None,
                "end_time": schedule.end_time.strftime("%H:%M") if schedule.end_time else None,
                "slot_duration_minutes": schedule.slot_duration_minutes,
                "break_start_time": schedule.break_start_time.strftime("%H:%M") if schedule.break_start_time else None,
                "break_end_time": schedule.break_end_time.strftime("%H:%M") if schedule.break_end_time else None,
                "max_patients_per_slot": schedule.max_patients_per_slot,
                "is_emergency_available": schedule.is_emergency_available,
                "is_active": schedule.is_active,
                "effective_from": schedule.effective_from,
                "effective_to": schedule.effective_to,
                "notes": schedule.notes,
                "created_at": schedule.created_at.isoformat(),
                "updated_at": schedule.updated_at.isoformat()
            })
        
        return {
            "doctor_id": str(doctor_id),
            "doctor_name": f"{doctor.user.first_name} {doctor.user.last_name}",
            "schedules": schedule_list
        }

    # ============================================================================
    # TASK 2.4 - APPOINTMENT OVERSIGHT (Admin View)
    # ============================================================================
    
    async def get_appointments(
        self, 
        page: int = 1, 
        limit: int = 50,
        status_filter: Optional[str] = None,
        doctor_id: Optional[str] = None,
        department_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated list of appointments for hospital oversight"""
        from app.models.patient import Appointment
        from app.models.doctor import DoctorProfile
        from app.models.hospital import Department
        from app.models.patient import PatientProfile
        
        offset = (page - 1) * limit
        
        # Build query with hospital filter
        query = select(Appointment).options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user),
            selectinload(Appointment.doctor),
            selectinload(Appointment.department)
        ).where(Appointment.hospital_id == self.hospital_id)
        
        # Filter by status
        if status_filter:
            query = query.where(Appointment.status == status_filter)
        
        # Filter by doctor
        if doctor_id:
            query = query.where(Appointment.doctor_id == uuid.UUID(doctor_id))
        
        # Filter by department
        if department_id:
            query = query.where(Appointment.department_id == uuid.UUID(department_id))
        
        # Filter by date range
        if date_from:
            query = query.where(Appointment.appointment_date >= date_from)
        if date_to:
            query = query.where(Appointment.appointment_date <= date_to)
        
        # Get total count
        count_query = select(func.count(Appointment.id)).where(Appointment.hospital_id == self.hospital_id)
        if status_filter:
            count_query = count_query.where(Appointment.status == status_filter)
        if doctor_id:
            count_query = count_query.where(Appointment.doctor_id == uuid.UUID(doctor_id))
        if department_id:
            count_query = count_query.where(Appointment.department_id == uuid.UUID(department_id))
        if date_from:
            count_query = count_query.where(Appointment.appointment_date >= date_from)
        if date_to:
            count_query = count_query.where(Appointment.appointment_date <= date_to)
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(Appointment.appointment_date.desc(), Appointment.appointment_time.desc())
        result = await self.db.execute(query)
        appointments = result.scalars().all()
        
        # Format response
        appointment_list = []
        for appointment in appointments:
            patient_name = f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}" if appointment.patient and appointment.patient.user else "Unknown"
            doctor_name = f"{appointment.doctor.user.first_name} {appointment.doctor.user.last_name}" if appointment.doctor and appointment.doctor.user else "Unknown"
            department_name = appointment.department.name if appointment.department else "Unknown"
            
            appointment_list.append({
                "id": str(appointment.id),
                "appointment_number": appointment.appointment_number,
                "patient_id": str(appointment.patient_id),
                "patient_name": patient_name,
                "patient_phone": appointment.patient.user.phone if appointment.patient and appointment.patient.user else None,
                "doctor_id": str(appointment.doctor_id),
                "doctor_name": doctor_name,
                "department_id": str(appointment.department_id) if appointment.department_id else None,
                "department_name": department_name,
                "appointment_date": appointment.appointment_date,
                "appointment_time": appointment.appointment_time.strftime("%H:%M") if appointment.appointment_time else None,
                "status": appointment.status,
                "appointment_type": appointment.appointment_type,
                "chief_complaint": appointment.chief_complaint,
                "notes": appointment.notes,
                "is_emergency": _appointment_is_emergency(appointment),
                "consultation_fee": float(appointment.consultation_fee) if appointment.consultation_fee else None,
                "created_at": appointment.created_at.isoformat(),
                "updated_at": appointment.updated_at.isoformat()
            })
        
        return {
            "appointments": appointment_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }
    
    async def get_appointment_details(self, appointment_id: uuid.UUID) -> Dict[str, Any]:
        """Get detailed appointment information for admin oversight"""
        from app.models.patient import Appointment
        from app.models.doctor import DoctorProfile
        from app.models.hospital import Department
        from app.models.patient import PatientProfile
        
        # Get appointment with all related data
        query = select(Appointment).options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user),
            selectinload(Appointment.doctor),
            selectinload(Appointment.department)
        ).where(
            and_(
                Appointment.id == appointment_id,
                Appointment.hospital_id == self.hospital_id
            )
        )
        
        result = await self.db.execute(query)
        appointment = result.scalar_one_or_none()
        
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "APPOINTMENT_NOT_FOUND", "message": "Appointment not found"}
            )
        
        # Format patient information
        patient_info = None
        if appointment.patient and appointment.patient.user:
            patient_info = {
                "id": str(appointment.patient_id),
                "name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
                "email": appointment.patient.user.email,
                "phone": appointment.patient.user.phone,
                "patient_id": appointment.patient.patient_id,
                "age": _age_from_date_of_birth(getattr(appointment.patient, "date_of_birth", None)),
                "gender": appointment.patient.gender,
                "blood_group": appointment.patient.blood_group
            }
        
        # Format doctor information
        doctor_info = None
        if appointment.doctor and appointment.doctor.user:
            doctor_info = {
                "id": str(appointment.doctor_id),
                "name": f"{appointment.doctor.user.first_name} {appointment.doctor.user.last_name}",
                "email": appointment.doctor.user.email,
                "phone": appointment.doctor.user.phone,
                "doctor_id": appointment.doctor.doctor_id,
                "specialization": appointment.doctor.specialization,
                "designation": appointment.doctor.designation
            }
        
        # Format department information
        department_info = None
        if appointment.department:
            department_info = {
                "id": str(appointment.department_id),
                "name": appointment.department.name,
                "code": appointment.department.code,
                "location": appointment.department.location
            }
        
        return {
            "id": str(appointment.id),
            "appointment_number": appointment.appointment_number,
            "patient": patient_info,
            "doctor": doctor_info,
            "department": department_info,
            "appointment_date": appointment.appointment_date,
            "appointment_time": appointment.appointment_time.strftime("%H:%M") if appointment.appointment_time else None,
            "status": appointment.status,
            "appointment_type": appointment.appointment_type,
            "chief_complaint": appointment.chief_complaint,
            "symptoms": appointment.symptoms,
            "notes": appointment.notes,
            "is_emergency": _appointment_is_emergency(appointment),
            "consultation_fee": float(appointment.consultation_fee) if appointment.consultation_fee else None,
            "payment_status": appointment.payment_status,
            "created_at": appointment.created_at.isoformat(),
            "updated_at": appointment.updated_at.isoformat(),
            "cancelled_at": appointment.cancelled_at.isoformat() if appointment.cancelled_at else None,
            "cancellation_reason": appointment.cancellation_reason
        }
    
    async def update_appointment_status(
        self, 
        appointment_id: uuid.UUID, 
        new_status: str,
        admin_notes: Optional[str] = None,
        cancellation_reason: Optional[str] = None,
        reschedule_date: Optional[str] = None,
        reschedule_time: Optional[str] = None,
        new_doctor_ref: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update appointment status with admin oversight. new_doctor_ref: doctor ref (DOC-xxx) or doctor name."""
        from app.models.patient import Appointment
        from app.core.utils import parse_time_string
        
        # Resolve new_doctor_ref to DoctorProfile if provided (use .user_id for appointment.doctor_id)
        resolved_new_doctor = None
        if new_doctor_ref:
            resolved_new_doctor = await self._get_hospital_doctor_by_ref_or_name(new_doctor_ref)
            if not resolved_new_doctor:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "DOCTOR_NOT_FOUND", "message": f"Doctor not found: {new_doctor_ref}"}
                )
        
        # Get appointment
        result = await self.db.execute(
            select(Appointment).where(
                and_(
                    Appointment.id == appointment_id,
                    Appointment.hospital_id == self.hospital_id
                )
            )
        )
        appointment = result.scalar_one_or_none()
        
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "APPOINTMENT_NOT_FOUND", "message": "Appointment not found"}
            )
        
        old_status = appointment.status
        
        # Validate status transition
        valid_statuses = ["SCHEDULED", "CONFIRMED", "IN_PROGRESS", "COMPLETED", "CANCELLED", "NO_SHOW"]
        if new_status not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_STATUS", "message": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"}
            )
        
        # Handle cancellation
        if new_status == "CANCELLED":
            if not cancellation_reason:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "CANCELLATION_REASON_REQUIRED", "message": "Cancellation reason is required"}
                )
            appointment.cancelled_at = datetime.utcnow()
            appointment.cancellation_reason = cancellation_reason
        
        # Handle rescheduling (change date/time while keeping status)
        if reschedule_date or reschedule_time:
            if reschedule_date:
                appointment.appointment_date = reschedule_date
            if reschedule_time:
                appointment.appointment_time = parse_time_string(reschedule_time)
            
            # If rescheduling, typically set status to SCHEDULED
            if new_status in ["CANCELLED", "NO_SHOW"]:
                new_status = "SCHEDULED"
        
        # Handle doctor reassignment (resolved_new_doctor already resolved from new_doctor_ref)
        if resolved_new_doctor:
            appointment.doctor_id = resolved_new_doctor.user_id
            if resolved_new_doctor.department_id:
                appointment.department_id = resolved_new_doctor.department_id
        
        # Update appointment
        appointment.status = new_status
        if admin_notes:
            current_notes = appointment.notes or ""
            appointment.notes = f"{current_notes}\n[ADMIN] {admin_notes}" if current_notes else f"[ADMIN] {admin_notes}"
        
        appointment.updated_at = datetime.utcnow()
        await self.db.commit()
        
        return {
            "appointment_id": str(appointment.id),
            "old_status": old_status,
            "new_status": new_status,
            "updated_fields": {
                "status": new_status,
                "reschedule_date": reschedule_date,
                "reschedule_time": reschedule_time,
                "new_doctor_ref": new_doctor_ref,
                "cancellation_reason": cancellation_reason,
                "admin_notes": admin_notes
            },
            "message": f"Appointment status updated from {old_status} to {new_status}"
        }

    # ============================================================================
    # TASK 2.5 - PATIENT MANAGEMENT (NON-MEDICAL)
    # ============================================================================
    
    async def get_patients(
        self, 
        page: int = 1, 
        limit: int = 50,
        active_only: bool = False,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated list of patients for non-medical admin oversight"""
        from app.models.patient import PatientProfile
        from app.models.user import User
        
        offset = (page - 1) * limit
        
        # Build query with hospital filter - NON-MEDICAL data only
        query = select(PatientProfile).options(
            selectinload(PatientProfile.user)
        ).where(PatientProfile.hospital_id == self.hospital_id)
        
        # Filter by active status
        if active_only:
            query = query.join(PatientProfile.user).where(User.is_active == True)
        
        # Search functionality (name, phone, email, patient ID)
        if search:
            search_term = f"%{search}%"
            query = query.join(PatientProfile.user).where(
                or_(
                    User.first_name.ilike(search_term),
                    User.last_name.ilike(search_term),
                    User.email.ilike(search_term),
                    User.phone.ilike(search_term),
                    PatientProfile.patient_id.ilike(search_term)
                )
            )
        
        # Get total count
        count_query = select(func.count(PatientProfile.id)).where(PatientProfile.hospital_id == self.hospital_id)
        if active_only:
            count_query = count_query.join(PatientProfile.user).where(User.is_active == True)
        if search:
            search_term = f"%{search}%"
            count_query = count_query.join(PatientProfile.user).where(
                or_(
                    User.first_name.ilike(search_term),
                    User.last_name.ilike(search_term),
                    User.email.ilike(search_term),
                    User.phone.ilike(search_term),
                    PatientProfile.patient_id.ilike(search_term)
                )
            )
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(PatientProfile.created_at.desc())
        result = await self.db.execute(query)
        patients = result.scalars().all()
        
        # Format response - EXCLUDE MEDICAL DATA
        patient_list = []
        for patient in patients:
            # Count appointments for this patient (non-medical metric)
            from app.models.patient import Appointment
            appointment_count_query = select(func.count()).select_from(
                select(1).where(
                    and_(
                        Appointment.patient_id == patient.id,
                        Appointment.hospital_id == self.hospital_id
                    )
                ).subquery()
            )
            appointment_count_result = await self.db.execute(appointment_count_query)
            appointment_count = appointment_count_result.scalar() or 0
            
            patient_list.append({
                "id": str(patient.id),
                "patient_id": patient.patient_id,
                "user_id": str(patient.user_id),
                # Basic demographic info (non-medical)
                "name": f"{patient.user.first_name} {patient.user.last_name}",
                "email": patient.user.email,
                "phone": patient.user.phone,
                "age": _age_from_date_of_birth(getattr(patient, "date_of_birth", None)),
                "gender": patient.gender,
                "date_of_birth": patient.date_of_birth,
                # Contact and administrative info
                "address": patient.address,
                "city": patient.city,
                "state": patient.state,
                "pincode": patient.pincode,
                "emergency_contact_name": patient.emergency_contact_name,
                "emergency_contact_phone": patient.emergency_contact_phone,
                "emergency_contact_relation": patient.emergency_contact_relation,
                # Account status
                "is_active": patient.user.is_active,
                "email_verified": patient.user.email_verified,
                "phone_verified": patient.user.phone_verified,
                "registration_date": patient.created_at.isoformat(),
                "last_updated": patient.updated_at.isoformat(),
                # Non-medical metrics
                "total_appointments": appointment_count,
                # EXPLICITLY EXCLUDE MEDICAL DATA
                # - No blood_group
                # - No allergies
                # - No medical_history
                # - No current_medications
                # - No chronic_conditions
            })
        
        return {
            "patients": patient_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            },
            "notice": "Medical records and sensitive health information are not accessible through admin interface"
        }
    
    async def update_patient_status(self, patient_id: uuid.UUID, is_active: bool) -> Dict[str, Any]:
        """Activate or deactivate patient account (non-medical admin action)"""
        from app.models.patient import PatientProfile
        from app.models.user import User
        
        # Get patient profile
        result = await self.db.execute(
            select(PatientProfile).options(selectinload(PatientProfile.user)).where(
                and_(
                    PatientProfile.id == patient_id,
                    PatientProfile.hospital_id == self.hospital_id
                )
            )
        )
        patient = result.scalar_one_or_none()
        
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PATIENT_NOT_FOUND", "message": "Patient not found"}
            )
        
        # Check if user has patient role
        user_roles = [role.name for role in patient.user.roles]
        if UserRole.PATIENT not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_PATIENT_USER", "message": "User is not a patient"}
            )
        
        # Update user account status
        old_status = patient.user.is_active
        patient.user.is_active = is_active
        patient.user.updated_at = datetime.utcnow()
        
        if not is_active:
            patient.user.status = UserStatus.BLOCKED.value
        else:
            patient.user.status = UserStatus.ACTIVE.value
        
        await self.db.commit()
        
        status_text = "activated" if is_active else "deactivated"
        
        return {
            "patient_id": str(patient.id),
            "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
            "old_status": old_status,
            "new_status": is_active,
            "status": patient.user.status
            if isinstance(patient.user.status, str)
            else getattr(patient.user.status, "value", str(patient.user.status)),
            "message": f"Patient account {status_text} successfully",
            "notice": "This action affects account access only. Medical records remain unchanged."
        }

    # ============================================================================
    # TASK 2.6 - BED & WARD MANAGEMENT
    # ============================================================================
    
    async def create_ward(self, ward_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new ward/unit"""
        from app.models.hospital import Ward
        from app.core.enums import WardType
        
        # ---------------------------------------------------------------------
        # INPUT NORMALIZATION (ALIGN WITH WardCreate SCHEMA)
        # ---------------------------------------------------------------------
        # Backward compatible handling of old/new field names coming from API
        # so that this service never crashes with KeyError for optional fields.

        # 1) Ward code: generate if not explicitly provided
        code = ward_data.get("code")
        if not code:
            # Auto-generate a simple, unique-ish ward code from name and floor_number
            name = ward_data.get("name", "").strip()
            base_code = name.upper().replace(" ", "_") if name else "WARD"
            floor_number = ward_data.get("floor_number")
            if floor_number is not None:
                code = f"{base_code}_F{floor_number}"
            else:
                code = base_code
            # Truncate to max 100 characters to match database column length
            if len(code) > 100:
                code = code[:97] + "..."
            ward_data["code"] = code

        # 2) Head nurse: accept either "head_nurse" (schema) or legacy "head_nurse_name"
        if "head_nurse_name" not in ward_data and ward_data.get("head_nurse"):
            ward_data["head_nurse_name"] = ward_data["head_nurse"]

        # 3) Phone: map generic "phone" from schema to nurse_station_phone if missing
        if "nurse_station_phone" not in ward_data and ward_data.get("phone"):
            ward_data["nurse_station_phone"] = ward_data["phone"]

        # 4) Floor: map integer floor_number to string floor if needed
        if "floor" not in ward_data and ward_data.get("floor_number") is not None:
            ward_data["floor"] = str(ward_data["floor_number"])

        # 5) Location details: reuse nurse_station_location if provided
        if "location_details" not in ward_data and ward_data.get("nurse_station_location"):
            ward_data["location_details"] = ward_data["nurse_station_location"]

        # 6) Booleans for isolation / emergency / oxygen – align with schema flags
        if "is_isolation_ward" not in ward_data and "isolation_capability" in ward_data:
            ward_data["is_isolation_ward"] = bool(ward_data["isolation_capability"])
        if "is_emergency_accessible" not in ward_data and "emergency_access" in ward_data:
            ward_data["is_emergency_accessible"] = bool(ward_data["emergency_access"])
        if "has_oxygen_supply" not in ward_data and "oxygen_supply" in ward_data:
            ward_data["has_oxygen_supply"] = bool(ward_data["oxygen_supply"])

        # 7) Visiting hours: accept either explicit start/end or a single "visiting_hours" string
        visiting_hours_start = None
        visiting_hours_end = None
        if ward_data.get("visiting_hours_start") or ward_data.get("visiting_hours_end"):
            from app.core.utils import parse_time_string
            if ward_data.get("visiting_hours_start"):
                visiting_hours_start = parse_time_string(ward_data["visiting_hours_start"])
            if ward_data.get("visiting_hours_end"):
                visiting_hours_end = parse_time_string(ward_data["visiting_hours_end"])
        elif ward_data.get("visiting_hours"):
            # Expect formats like "10:00 AM - 8:00 PM"
            from app.core.utils import parse_time_string
            raw = str(ward_data["visiting_hours"])
            parts = raw.split("-")
            if len(parts) == 2:
                start_raw = parts[0].strip()
                end_raw = parts[1].strip()
                try:
                    visiting_hours_start = parse_time_string(start_raw)
                    visiting_hours_end = parse_time_string(end_raw)
                except Exception:
                    # If parsing fails, keep them as None instead of breaking ward creation
                    visiting_hours_start = None
                    visiting_hours_end = None

        # Check if ward code already exists in this hospital
        existing_ward = await self.db.execute(
            select(Ward).where(
                and_(
                    Ward.hospital_id == self.hospital_id,
                    Ward.code == ward_data['code']
                )
            )
        )
        if existing_ward.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "WARD_CODE_EXISTS", "message": "Ward with this code already exists"}
            )
        
        # Validate ward type
        ward_type = ward_data['ward_type']
        if ward_type not in [wt.value for wt in WardType]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_WARD_TYPE", "message": f"Invalid ward type. Must be one of: {', '.join([wt.value for wt in WardType])}"}
            )
        
        # Validate head nurse if provided
        head_nurse_id = None
        head_nurse_name = ward_data.get('head_nurse_name')
        if head_nurse_name:
            head_nurse = await self._get_staff_by_name(head_nurse_name)
            if not head_nurse:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "HEAD_NURSE_NOT_FOUND", "message": f"Nurse '{head_nurse_name}' not found in this hospital"}
                )
            
            # Verify the staff member has NURSE role
            user_roles = [role.name for role in head_nurse.roles]
            if UserRole.NURSE not in user_roles:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "NOT_A_NURSE", "message": f"Staff member '{head_nurse_name}' is not a nurse"}
                )
            
            head_nurse_id = head_nurse.id
        
        # Create ward
        ward = Ward(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            name=ward_data['name'],
            code=ward_data['code'],
            ward_type=ward_type,
            description=ward_data.get('description'),
            floor=ward_data.get('floor'),
            building=ward_data.get('building'),
            location_details=ward_data.get('location_details'),
            total_beds=ward_data.get('total_beds', 0),
            nurse_station_phone=ward_data.get('nurse_station_phone'),
            head_nurse_id=head_nurse_id,
            is_isolation_ward=ward_data.get('is_isolation_ward', False),
            is_emergency_accessible=ward_data.get('is_emergency_accessible', True),
            visiting_hours_start=visiting_hours_start,
            visiting_hours_end=visiting_hours_end,
            has_oxygen_supply=ward_data.get('has_oxygen_supply', False),
            has_suction=ward_data.get('has_suction', False),
            has_cardiac_monitor=ward_data.get('has_cardiac_monitor', False),
            has_ventilator_support=ward_data.get('has_ventilator_support', False),
            settings=ward_data.get('settings', {})
        )
        
        self.db.add(ward)
        await self.db.flush()
        out = {
            "ward_id": str(ward.id),
            "name": ward.name,
            "code": ward.code,
            "ward_type": ward.ward_type,
            "message": "Ward created successfully",
        }
        await self.db.commit()
        return out
    
    async def get_wards(
        self, 
        page: int = 1, 
        limit: int = 50,
        ward_type: Optional[str] = None,
        active_only: bool = False
    ) -> Dict[str, Any]:
        """Get paginated list of wards"""
        from app.models.hospital import Ward
        
        offset = (page - 1) * limit
        
        # Build query with hospital filter
        query = select(Ward).options(
            selectinload(Ward.head_nurse),
            selectinload(Ward.beds)
        ).where(Ward.hospital_id == self.hospital_id)
        
        # Filter by ward type
        if ward_type:
            query = query.where(Ward.ward_type == ward_type)
        
        if active_only:
            query = query.where(Ward.is_active == True)
        
        # Get total count
        count_query = select(func.count(Ward.id)).where(Ward.hospital_id == self.hospital_id)
        if ward_type:
            count_query = count_query.where(Ward.ward_type == ward_type)
        if active_only:
            count_query = count_query.where(Ward.is_active == True)
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(Ward.name.asc())
        result = await self.db.execute(query)
        wards = result.scalars().all()
        
        # Format response
        ward_list = []
        for ward in wards:
            # Calculate bed statistics
            total_beds = len(ward.beds)
            available_beds = len([bed for bed in ward.beds if bed.status == "AVAILABLE"])
            occupied_beds = len([bed for bed in ward.beds if bed.status == "OCCUPIED"])
            maintenance_beds = len([bed for bed in ward.beds if bed.status == "MAINTENANCE"])
            
            head_nurse_name = None
            if ward.head_nurse:
                head_nurse_name = f"{ward.head_nurse.first_name} {ward.head_nurse.last_name}"
            
            ward_list.append({
                "id": str(ward.id),
                "name": ward.name,
                "code": ward.code,
                "ward_type": ward.ward_type,
                "description": ward.description,
                "floor": ward.floor,
                "building": ward.building,
                "location_details": ward.location_details,
                "total_beds": total_beds,
                "bed_statistics": {
                    "total": total_beds,
                    "available": available_beds,
                    "occupied": occupied_beds,
                    "maintenance": maintenance_beds,
                    "occupancy_rate": round((occupied_beds / total_beds * 100) if total_beds > 0 else 0, 1)
                },
                "nurse_station_phone": ward.nurse_station_phone,
                "head_nurse_id": str(ward.head_nurse_id) if ward.head_nurse_id else None,
                "head_nurse_name": head_nurse_name,
                "is_isolation_ward": ward.is_isolation_ward,
                "is_emergency_accessible": ward.is_emergency_accessible,
                "visiting_hours_start": ward.visiting_hours_start.strftime("%H:%M") if ward.visiting_hours_start else None,
                "visiting_hours_end": ward.visiting_hours_end.strftime("%H:%M") if ward.visiting_hours_end else None,
                "facilities": {
                    "oxygen_supply": ward.has_oxygen_supply,
                    "suction": ward.has_suction,
                    "cardiac_monitor": ward.has_cardiac_monitor,
                    "ventilator_support": ward.has_ventilator_support
                },
                "is_active": ward.is_active,
                "created_at": ward.created_at.isoformat(),
                "updated_at": ward.updated_at.isoformat()
            })
        
        return {
            "wards": ward_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }
    
    async def update_ward(self, ward_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update ward information.

        Maps WardUpdate / API field names to SQLAlchemy columns. Never assigns relationship
        names like ``head_nurse`` directly (that triggers async MissingGreenlet).
        """
        from app.models.hospital import Ward
        from app.core.utils import parse_time_string

        result = await self.db.execute(
            select(Ward).where(
                and_(
                    Ward.id == ward_id,
                    Ward.hospital_id == self.hospital_id,
                )
            )
        )
        ward = result.scalar_one_or_none()

        if not ward:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WARD_NOT_FOUND", "message": "Ward not found"},
            )

        data = dict(update_data)

        if "code" in data and data["code"] is not None and data["code"] != ward.code:
            existing_ward = await self.db.execute(
                select(Ward).where(
                    and_(
                        Ward.hospital_id == self.hospital_id,
                        Ward.code == data["code"],
                        Ward.id != ward_id,
                    )
                )
            )
            if existing_ward.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "WARD_CODE_EXISTS", "message": "Ward with this code already exists"},
                )

        # head_nurse (API) -> head_nurse_id (FK). Do not setattr(head_nurse, ...) on the model.
        if "head_nurse" in data:
            hn_raw = data.pop("head_nurse")
            if hn_raw is not None and str(hn_raw).strip():
                hn_name = str(hn_raw).strip()
                head_nurse_user = await self._get_staff_by_name(hn_name)
                if not head_nurse_user:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={
                            "code": "HEAD_NURSE_NOT_FOUND",
                            "message": f"Nurse '{hn_name}' not found in this hospital",
                        },
                    )
                nurse_roles = [r.name for r in head_nurse_user.roles]
                if UserRole.NURSE not in nurse_roles:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "NOT_A_NURSE",
                            "message": f"Staff member '{hn_name}' is not a nurse",
                        },
                    )
                data["head_nurse_id"] = head_nurse_user.id
            else:
                data["head_nurse_id"] = None

        if "head_nurse_id" in data:
            hid = data["head_nurse_id"]
            if hid:
                hid_uuid = hid if isinstance(hid, uuid.UUID) else uuid.UUID(str(hid))
                head_nurse = await self._get_hospital_staff_user(hid_uuid)
                if not head_nurse:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "HEAD_NURSE_NOT_FOUND", "message": "Head nurse not found in this hospital"},
                    )
                nurse_roles = [r.name for r in head_nurse.roles]
                if UserRole.NURSE not in nurse_roles:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "NOT_A_NURSE",
                            "message": "Selected user is not a nurse in this hospital",
                        },
                    )
                data["head_nurse_id"] = hid_uuid
            else:
                data["head_nurse_id"] = None

        if "phone" in data and data["phone"] is not None:
            data["nurse_station_phone"] = data.pop("phone")

        if "floor_number" in data and data["floor_number"] is not None:
            data["floor"] = str(data.pop("floor_number"))

        if "nurse_station_location" in data:
            loc = data.pop("nurse_station_location")
            if loc is not None:
                data["location_details"] = loc

        for api_key, model_key in (
            ("emergency_access", "is_emergency_accessible"),
            ("isolation_capability", "is_isolation_ward"),
            ("oxygen_supply", "has_oxygen_supply"),
        ):
            if api_key in data:
                data[model_key] = bool(data.pop(api_key))

        facilities_update = data.pop("facilities", None)
        if facilities_update is not None:
            st = dict(ward.settings or {})
            st["facilities"] = facilities_update
            ward.settings = st

        visiting_hours_start = None
        visiting_hours_end = None
        if data.get("visiting_hours_start") or data.get("visiting_hours_end"):
            if data.get("visiting_hours_start"):
                visiting_hours_start = parse_time_string(str(data.pop("visiting_hours_start")))
            if data.get("visiting_hours_end"):
                visiting_hours_end = parse_time_string(str(data.pop("visiting_hours_end")))
        elif data.get("visiting_hours"):
            raw = str(data.pop("visiting_hours"))
            parts = raw.split("-")
            if len(parts) == 2:
                try:
                    visiting_hours_start = parse_time_string(parts[0].strip())
                    visiting_hours_end = parse_time_string(parts[1].strip())
                except Exception:
                    visiting_hours_start = None
                    visiting_hours_end = None
        if visiting_hours_start is not None:
            data["visiting_hours_start"] = visiting_hours_start
        if visiting_hours_end is not None:
            data["visiting_hours_end"] = visiting_hours_end

        allowed_columns = {
            "name",
            "code",
            "ward_type",
            "description",
            "floor",
            "building",
            "location_details",
            "total_beds",
            "nurse_station_phone",
            "head_nurse_id",
            "is_isolation_ward",
            "is_emergency_accessible",
            "visiting_hours_start",
            "visiting_hours_end",
            "has_oxygen_supply",
            "has_suction",
            "has_cardiac_monitor",
            "has_ventilator_support",
            "settings",
        }

        for field, value in list(data.items()):
            if field not in allowed_columns:
                continue
            setattr(ward, field, value)

        ward.updated_at = datetime.utcnow()
        ward_id_str = str(ward.id)
        await self.db.commit()

        return {
            "ward_id": ward_id_str,
            "message": "Ward updated successfully",
        }

    async def update_ward_status(self, ward_id: uuid.UUID, is_active: bool) -> Dict[str, Any]:
        """Enable or disable ward"""
        from app.models.hospital import Ward
        
        # Get ward
        result = await self.db.execute(
            select(Ward).where(
                and_(
                    Ward.id == ward_id,
                    Ward.hospital_id == self.hospital_id
                )
            )
        )
        ward = result.scalar_one_or_none()
        
        if not ward:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WARD_NOT_FOUND", "message": "Ward not found"}
            )
        
        # Update status
        old_status = ward.is_active
        ward.is_active = is_active
        ward.updated_at = datetime.utcnow()

        ward_id_str = str(ward.id)
        await self.db.commit()

        status_text = "enabled" if is_active else "disabled"

        return {
            "ward_id": ward_id_str,
            "old_status": old_status,
            "new_status": is_active,
            "message": f"Ward {status_text} successfully",
        }
    
    async def create_bed(self, bed_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new bed"""
        from app.models.hospital import Bed, Ward
        from app.core.enums import BedStatus
        
        # ---------------------------------------------------------------------
        # INPUT NORMALIZATION (ALIGN WITH BedCreate SCHEMA)
        # ---------------------------------------------------------------------

        # 1) Ensure ward_name exists (schema already enforces this)
        ward_name = bed_data['ward_name']

        # 2) Generate bed_code if not explicitly provided
        bed_code = bed_data.get("bed_code")
        if not bed_code:
            # Build a readable unique code from ward name and bed_number
            bed_number = bed_data.get("bed_number", "").strip()
            safe_ward = ward_name.upper().replace(" ", "_") if ward_name else "WARD"
            safe_bed = bed_number.upper().replace(" ", "_") if bed_number else "BED"
            bed_code = f"{safe_ward}-BED-{safe_bed}"
            bed_data["bed_code"] = bed_code

        # 3) Map boolean convenience flags if coming from simpler payloads
        #    (BedCreate already has booleans named has_oxygen / has_monitor etc.)
        if "has_cardiac_monitor" not in bed_data and bed_data.get("has_monitor") is not None:
            bed_data["has_cardiac_monitor"] = bool(bed_data["has_monitor"])
        
        # Find ward by name
        ward = await self._get_ward_by_name(ward_name)
        if not ward:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WARD_NOT_FOUND", "message": f"Ward '{ward_name}' not found in this hospital"}
            )
        
        # Check if bed code already exists in this hospital
        existing_bed = await self.db.execute(
            select(Bed).where(
                and_(
                    Bed.hospital_id == self.hospital_id,
                    Bed.bed_code == bed_data['bed_code']
                )
            )
        )
        if existing_bed.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "BED_CODE_EXISTS", "message": "Bed with this code already exists"}
            )
        
        # Validate bed status
        bed_status = bed_data.get('status', BedStatus.AVAILABLE)
        if bed_status not in [bs.value for bs in BedStatus]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_BED_STATUS", "message": f"Invalid bed status. Must be one of: {', '.join([bs.value for bs in BedStatus])}"}
            )
        
        # Create bed
        bed = Bed(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            ward_id=ward.id,
            bed_number=bed_data['bed_number'],
            bed_code=bed_data['bed_code'],
            status=bed_status,
            bed_type=bed_data.get('bed_type', 'STANDARD'),
            floor=bed_data.get('floor'),
            room_number=bed_data.get('room_number'),
            bed_position=bed_data.get('bed_position'),
            has_oxygen=bed_data.get('has_oxygen', False),
            has_suction=bed_data.get('has_suction', False),
            has_cardiac_monitor=bed_data.get('has_cardiac_monitor', False),
            has_ventilator=bed_data.get('has_ventilator', False),
            has_iv_pole=bed_data.get('has_iv_pole', True),
            daily_rate=bed_data.get('daily_rate', 0),
            notes=bed_data.get('notes'),
            settings=bed_data.get('settings', {})
        )
        
        ward_name_str = ward.name
        ward_id_str = str(ward.id)
        self.db.add(bed)
        await self.db.flush()
        out = {
            "bed_id": str(bed.id),
            "bed_code": bed.bed_code,
            "bed_number": bed.bed_number,
            "ward_name": ward_name_str,
            "ward_id": ward_id_str,
            "status": bed.status,
            "message": "Bed created successfully",
        }
        await self.db.commit()
        return out
    
    async def get_beds(
        self, 
        page: int = 1, 
        limit: int = 50,
        ward_id: Optional[str] = None,
        status_filter: Optional[str] = None,
        bed_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated list of beds"""
        from app.models.hospital import Bed, Ward
        
        offset = (page - 1) * limit
        
        # Build query with hospital filter
        query = select(Bed).options(
            selectinload(Bed.ward),
            selectinload(Bed.current_patient).selectinload(PatientProfile.user),
        ).where(Bed.hospital_id == self.hospital_id)
        
        # Filter by ward
        if ward_id:
            query = query.where(Bed.ward_id == uuid.UUID(ward_id))
        
        # Filter by status
        if status_filter:
            query = query.where(Bed.status == status_filter)
        
        # Filter by bed type
        if bed_type:
            query = query.where(Bed.bed_type == bed_type)
        
        # Get total count
        count_query = select(func.count(Bed.id)).where(Bed.hospital_id == self.hospital_id)
        if ward_id:
            count_query = count_query.where(Bed.ward_id == uuid.UUID(ward_id))
        if status_filter:
            count_query = count_query.where(Bed.status == status_filter)
        if bed_type:
            count_query = count_query.where(Bed.bed_type == bed_type)
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(Bed.ward_id.asc(), Bed.bed_number.asc())
        result = await self.db.execute(query)
        beds = result.scalars().all()
        
        # Format response
        bed_list = []
        for bed in beds:
            current_patient_info = None
            if bed.current_patient:
                current_patient_info = {
                    "id": str(bed.current_patient.id),
                    "patient_id": bed.current_patient.patient_id,
                    "name": f"{bed.current_patient.user.first_name} {bed.current_patient.user.last_name}" if bed.current_patient.user else "Unknown"
                }
            
            bed_list.append({
                "id": str(bed.id),
                "bed_code": bed.bed_code,
                "bed_number": bed.bed_number,
                "ward": {
                    "id": str(bed.ward_id),
                    "name": bed.ward.name,
                    "code": bed.ward.code,
                    "ward_type": bed.ward.ward_type
                },
                "status": bed.status,
                "bed_type": bed.bed_type,
                "floor": bed.floor,
                "room_number": bed.room_number,
                "bed_position": bed.bed_position,
                "equipment": {
                    "oxygen": bed.has_oxygen,
                    "suction": bed.has_suction,
                    "cardiac_monitor": bed.has_cardiac_monitor,
                    "ventilator": bed.has_ventilator,
                    "iv_pole": bed.has_iv_pole
                },
                "current_patient": current_patient_info,
                "occupied_since": bed.occupied_since.isoformat() if bed.occupied_since else None,
                "last_cleaned": bed.last_cleaned.isoformat() if bed.last_cleaned else None,
                "daily_rate": float(bed.daily_rate) if bed.daily_rate else None,
                "maintenance_notes": bed.maintenance_notes,
                "notes": bed.notes,
                "is_active": bed.is_active,
                "created_at": bed.created_at.isoformat(),
                "updated_at": bed.updated_at.isoformat()
            })
        
        return {
            "beds": bed_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }
    
    async def get_bed_details(self, bed_id: uuid.UUID) -> Dict[str, Any]:
        """Get detailed bed information"""
        from app.models.hospital import Bed
        
        # Get bed with ward and patient details
        query = select(Bed).options(
            selectinload(Bed.ward),
            selectinload(Bed.current_patient).selectinload(PatientProfile.user)
        ).where(
            and_(
                Bed.id == bed_id,
                Bed.hospital_id == self.hospital_id
            )
        )
        
        result = await self.db.execute(query)
        bed = result.scalar_one_or_none()
        
        if not bed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "BED_NOT_FOUND", "message": "Bed not found"}
            )
        
        # Format ward information
        ward_info = {
            "id": str(bed.ward_id),
            "name": bed.ward.name,
            "code": bed.ward.code,
            "ward_type": bed.ward.ward_type,
            "floor": bed.ward.floor,
            "building": bed.ward.building
        }
        
        # Format current patient information (if occupied)
        current_patient_info = None
        if bed.current_patient and bed.current_patient.user:
            current_patient_info = {
                "id": str(bed.current_patient.id),
                "patient_id": bed.current_patient.patient_id,
                "name": f"{bed.current_patient.user.first_name} {bed.current_patient.user.last_name}",
                "date_of_birth": bed.current_patient.date_of_birth,
                "gender": bed.current_patient.gender,
                "phone": bed.current_patient.user.phone,
            }
        
        return {
            "id": str(bed.id),
            "bed_code": bed.bed_code,
            "bed_number": bed.bed_number,
            "ward": ward_info,
            "status": bed.status,
            "bed_type": bed.bed_type,
            "floor": bed.floor,
            "room_number": bed.room_number,
            "bed_position": bed.bed_position,
            "equipment": {
                "oxygen": bed.has_oxygen,
                "suction": bed.has_suction,
                "cardiac_monitor": bed.has_cardiac_monitor,
                "ventilator": bed.has_ventilator,
                "iv_pole": bed.has_iv_pole
            },
            "current_patient": current_patient_info,
            "occupied_since": bed.occupied_since.isoformat() if bed.occupied_since else None,
            "last_cleaned": bed.last_cleaned.isoformat() if bed.last_cleaned else None,
            "daily_rate": float(bed.daily_rate) if bed.daily_rate else None,
            "maintenance_notes": bed.maintenance_notes,
            "notes": bed.notes,
            "settings": bed.settings if isinstance(bed.settings, dict) else {},
            "is_active": bed.is_active,
            "created_at": bed.created_at.isoformat(),
            "updated_at": bed.updated_at.isoformat()
        }
    
    async def update_bed_status(
        self, 
        bed_id: uuid.UUID, 
        new_status: str,
        maintenance_notes: Optional[str] = None,
        patient_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update bed status"""
        from app.models.hospital import Bed
        from app.core.enums import BedStatus
        
        # Get bed
        result = await self.db.execute(
            select(Bed).where(
                and_(
                    Bed.id == bed_id,
                    Bed.hospital_id == self.hospital_id
                )
            )
        )
        bed = result.scalar_one_or_none()
        
        if not bed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "BED_NOT_FOUND", "message": "Bed not found"}
            )
        
        allowed_statuses = {bs.value for bs in BedStatus}
        new_status_norm = str(new_status).strip().upper()
        if new_status_norm not in allowed_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_BED_STATUS",
                    "message": f"Invalid bed status. Must be one of: {', '.join(sorted(allowed_statuses))}",
                },
            )

        old_status = bed.status
        old_status_label = (
            old_status.value if isinstance(old_status, BedStatus) else str(old_status)
        )

        # Handle status-specific logic (compare using normalized enum values)
        if new_status_norm == BedStatus.OCCUPIED.value:
            if not patient_id or not str(patient_id).strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "PATIENT_ID_REQUIRED",
                        "message": "patient_id is required when marking bed as OCCUPIED (profile UUID or hospital ref e.g. PAT-001)",
                    },
                )

            profile_uuid = await self._resolve_patient_profile_uuid(patient_id)
            bed.current_patient_id = profile_uuid
            bed.occupied_since = datetime.utcnow()
            patient_id_out = str(profile_uuid)

        elif new_status_norm == BedStatus.AVAILABLE.value:
            # Clear patient assignment
            bed.current_patient_id = None
            bed.occupied_since = None
            bed.last_cleaned = datetime.utcnow()
            patient_id_out = None

        elif new_status_norm == BedStatus.MAINTENANCE.value:
            # Clear patient assignment and add maintenance notes
            bed.current_patient_id = None
            bed.occupied_since = None
            if maintenance_notes:
                bed.maintenance_notes = maintenance_notes
            patient_id_out = None

        else:
            patient_id_out = None

        # Update bed status (persist canonical uppercase value)
        bed.status = new_status_norm
        bed.updated_at = datetime.utcnow()

        bed_id_str = str(bed.id)
        bed_code_str = bed.bed_code
        await self.db.commit()

        return {
            "bed_id": bed_id_str,
            "bed_code": bed_code_str,
            "old_status": old_status_label,
            "new_status": new_status_norm,
            "patient_id": patient_id_out,
            "maintenance_notes": maintenance_notes,
            "message": f"Bed status updated from {old_status_label} to {new_status_norm}",
        }

    # ============================================================================
    # TASK 2.7 - BED ASSIGNMENT (ADMISSION FLOW)
    # ============================================================================
    
    async def create_admission(self, admission_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new patient admission. Uses patient_ref, admitting_doctor (name/ref), department (name)."""
        from app.models.patient import Admission
        from app.core.enums import AdmissionStatus
        
        # Resolve patient_ref to PatientProfile.id
        patient_ref = admission_data.get("patient_ref") or admission_data.get("patient_id")
        if not patient_ref:
            raise HTTPException(status_code=400, detail={"code": "MISSING_PATIENT_REF", "message": "patient_ref is required"})
        if isinstance(patient_ref, uuid.UUID):
            patient = await self._get_hospital_patient(patient_ref)
        else:
            pr = await self.db.execute(
                select(PatientProfile).where(
                    and_(
                        PatientProfile.hospital_id == self.hospital_id,
                        PatientProfile.patient_id == str(patient_ref).strip()
                    )
                ).limit(1)
            )
            patient = pr.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PATIENT_NOT_FOUND", "message": f"Patient not found: {patient_ref}"}
            )
        patient_id = patient.id
        
        # Resolve admitting_doctor (name or ref) to doctor user id
        admitting_doctor = admission_data.get("admitting_doctor") or admission_data.get("doctor_id")
        if not admitting_doctor:
            raise HTTPException(status_code=400, detail={"code": "MISSING_DOCTOR", "message": "admitting_doctor is required"})
        doctor_profile = await self._get_hospital_doctor_by_ref_or_name(str(admitting_doctor).strip())
        if not doctor_profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DOCTOR_NOT_FOUND", "message": f"Doctor not found: {admitting_doctor}"}
            )
        doctor_id = doctor_profile.user_id
        
        # Resolve department (name) to department id
        dept_name = (admission_data.get("department") or "").strip()
        if not dept_name:
            raise HTTPException(status_code=400, detail={"code": "MISSING_DEPARTMENT", "message": "department (name) is required"})
        dept_result = await self.db.execute(
            select(Department).where(
                and_(
                    Department.hospital_id == self.hospital_id,
                    func.lower(Department.name) == dept_name.lower()
                )
            ).limit(1)
        )
        department = dept_result.scalar_one_or_none()
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DEPARTMENT_NOT_FOUND", "message": f"Department not found: {dept_name}"}
            )
        department_id = department.id
        
        # Build admission_date (DateTime from date + time)
        adate_str = admission_data.get("admission_date") or datetime.utcnow().strftime("%Y-%m-%d")
        atime_str = admission_data.get("admission_time") or "00:00"
        try:
            from datetime import datetime as dt
            admission_dt = dt.fromisoformat(f"{adate_str}T{atime_str.replace('.', ':')}")
        except Exception:
            admission_dt = datetime.utcnow()
        
        # Generate admission number
        admission_number = f"ADM{datetime.utcnow().strftime('%Y%m%d')}{str(uuid.uuid4())[:8].upper()}"
        
        # Create admission
        admission = Admission(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            patient_id=patient_id,
            doctor_id=doctor_id,
            department_id=department_id,
            admission_number=admission_number,
            admission_date=admission_dt,
            admission_type=admission_data.get('admission_type', 'REGULAR'),
            chief_complaint=admission_data.get('chief_complaint') or admission_data.get('diagnosis') or "Admitted",
            provisional_diagnosis=admission_data.get('diagnosis'),
            admission_notes=admission_data.get('admission_notes'),
        )
        
        self.db.add(admission)
        admission_id_str = str(admission.id)
        admission_status = (
            getattr(admission, "status", "PENDING") if hasattr(admission, "status") else "PENDING"
        )
        patient_ref_out = getattr(patient, "patient_id", None) or str(patient_id)
        await self.db.commit()

        return {
            "admission_id": admission_id_str,
            "admission_number": admission_number,
            "patient_ref": patient_ref_out,
            "doctor_id": str(doctor_id),
            "status": admission_status,
            "message": "Admission created successfully",
        }
    
    async def assign_bed_to_admission(
        self, 
        admission_id: uuid.UUID, 
        bed_id: uuid.UUID,
        admission_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Assign bed to admission and update statuses"""
        from app.models.patient import Admission
        from app.models.hospital import Bed
        from app.core.enums import AdmissionStatus, BedStatus
        
        # Get admission
        admission_result = await self.db.execute(
            select(Admission).options(
                selectinload(Admission.patient).selectinload(PatientProfile.user)
            ).where(
                and_(
                    Admission.id == admission_id,
                    Admission.hospital_id == self.hospital_id
                )
            )
        )
        admission = admission_result.scalar_one_or_none()
        
        if not admission:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ADMISSION_NOT_FOUND", "message": "Admission not found"}
            )
        
        # Check if admission is in valid state for bed assignment
        if admission.status not in [AdmissionStatus.PENDING, AdmissionStatus.ADMITTED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ADMISSION_STATUS", "message": f"Cannot assign bed to admission with status {admission.status}"}
            )
        
        # Get bed
        bed_result = await self.db.execute(
            select(Bed).options(selectinload(Bed.ward)).where(
                and_(
                    Bed.id == bed_id,
                    Bed.hospital_id == self.hospital_id
                )
            )
        )
        bed = bed_result.scalar_one_or_none()
        
        if not bed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "BED_NOT_FOUND", "message": "Bed not found"}
            )
        
        # Check if bed is available
        if bed.status != BedStatus.AVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "BED_NOT_AVAILABLE", "message": f"Bed is not available (current status: {bed.status})"}
            )
        
        # Check if patient already has an active bed assignment
        if admission.bed_id and admission.status == AdmissionStatus.ADMITTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "BED_ALREADY_ASSIGNED", "message": "Patient already has a bed assigned"}
            )
        
        # Assign bed to admission (status is derived from is_active and discharge_date)
        admission.bed_id = bed_id
        # Keep is_active=True and discharge_date=None so status is ADMITTED
        if hasattr(admission, "actual_admission_date"):
            admission.actual_admission_date = datetime.utcnow().date().isoformat()
        if hasattr(admission, "actual_admission_time"):
            admission.actual_admission_time = datetime.utcnow().time()
        
        if admission_notes:
            current_notes = admission.admission_notes or ""
            admission.admission_notes = f"{current_notes}\n[BED ASSIGNMENT] {admission_notes}" if current_notes else f"[BED ASSIGNMENT] {admission_notes}"
        
        # Update bed status
        bed.status = BedStatus.OCCUPIED
        bed.current_patient_id = admission.patient_id
        bed.occupied_since = datetime.utcnow()
        bed.updated_at = datetime.utcnow()
        
        admission.updated_at = datetime.utcnow()

        admission_id_str = str(admission.id)
        admission_number_out = admission.admission_number
        pu = admission.patient.user if admission.patient and admission.patient.user else None
        patient_name_out = f"{pu.first_name} {pu.last_name}" if pu else "Unknown"
        bed_code_out = bed.bed_code
        ward_name_out = bed.ward.name if bed.ward else None
        admission_status_out = admission.status
        assigned_at_out = getattr(
            admission, "actual_admission_date", datetime.utcnow().date().isoformat()
        )

        await self.db.commit()

        return {
            "admission_id": admission_id_str,
            "admission_number": admission_number_out,
            "patient_name": patient_name_out,
            "bed_code": bed_code_out,
            "ward_name": ward_name_out,
            "status": admission_status_out,
            "assigned_at": assigned_at_out,
            "message": "Bed assigned successfully and patient admitted",
        }
    
    async def discharge_patient(
        self, 
        admission_id: uuid.UUID,
        discharge_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Discharge patient and release bed"""
        from app.models.patient import Admission, DischargeSummary
        from app.models.hospital import Bed
        from app.core.enums import AdmissionStatus, BedStatus
        
        # Get admission with bed and patient details
        admission_result = await self.db.execute(
            select(Admission).options(
                selectinload(Admission.patient).selectinload(PatientProfile.user),
                selectinload(Admission.bed).selectinload(Bed.ward),
                selectinload(Admission.doctor)
            ).where(
                and_(
                    Admission.id == admission_id,
                    Admission.hospital_id == self.hospital_id
                )
            )
        )
        admission = admission_result.scalar_one_or_none()
        
        if not admission:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ADMISSION_NOT_FOUND", "message": "Admission not found"}
            )
        
        # Check if admission is in valid state for discharge (status derived from is_active and discharge_date)
        if admission.status != AdmissionStatus.ADMITTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ADMISSION_STATUS", "message": f"Cannot discharge admission with status {admission.status}"}
            )
        
        # Calculate length of stay
        admission_date_val = getattr(admission, "actual_admission_date", None) or admission.admission_date
        discharge_date = datetime.utcnow()
        try:
            if admission_date_val and hasattr(admission_date_val, "__sub__"):
                length_of_stay = (discharge_date - admission_date_val).days + 1
            else:
                length_of_stay = 0
        except Exception:
            length_of_stay = 0
        
        # Update admission: set is_active=False and discharge_date (status is derived)
        admission.is_active = False
        admission.discharge_date = discharge_date
        admission.discharge_type = discharge_data.get('discharge_type', 'REGULAR')
        if hasattr(admission, "discharge_notes"):
            admission.discharge_notes = discharge_data.get('discharge_notes')
        if hasattr(admission, "length_of_stay"):
            admission.length_of_stay = length_of_stay
        admission.updated_at = datetime.utcnow()
        
        # Release bed if assigned
        if admission.bed_id:
            bed_result = await self.db.execute(
                select(Bed).where(Bed.id == admission.bed_id)
            )
            bed = bed_result.scalar_one_or_none()
            
            if bed:
                bed.status = BedStatus.AVAILABLE
                bed.current_patient_id = None
                bed.occupied_since = None
                bed.last_cleaned = datetime.utcnow()  # Mark as needing cleaning
                bed.updated_at = datetime.utcnow()
        
        # Create discharge summary if provided
        discharge_summary_id = None
        if discharge_data.get('create_discharge_summary', False):
            final_diag = discharge_data.get('final_diagnosis') or getattr(admission, "diagnosis", None) or admission.provisional_diagnosis or ""
            discharge_summary = DischargeSummary(
                id=uuid.uuid4(),
                hospital_id=self.hospital_id,
                patient_id=admission.patient_id,
                doctor_id=admission.doctor_id,
                admission_date=admission.admission_date,
                discharge_date=discharge_date,
                length_of_stay=length_of_stay,
                chief_complaint=admission.chief_complaint,
                final_diagnosis=final_diag,
                hospital_course=discharge_data.get('treatment_summary'),
                medications_on_discharge=discharge_data.get('medications_on_discharge', []),
                follow_up_instructions=discharge_data.get('follow_up_instructions'),
                discharge_type=discharge_data.get('discharge_type', 'REGULAR')
            )
            self.db.add(discharge_summary)
            await self.db.flush()
            if hasattr(Admission, "discharge_summary_id"):
                admission.discharge_summary_id = discharge_summary.id
            discharge_summary_id = str(discharge_summary.id)

        admission_id_str = str(admission.id)
        admission_number_out = admission.admission_number
        u = (
            admission.patient.user
            if admission.patient and getattr(admission.patient, "user", None)
            else None
        )
        patient_name_out = f"{u.first_name} {u.last_name}" if u else "Unknown"
        bed_code_out = admission.bed.bed_code if admission.bed else None
        ward_name_out = (
            admission.bed.ward.name
            if admission.bed and getattr(admission.bed, "ward", None)
            else None
        )
        discharge_date_out = (
            admission.discharge_date.isoformat()
            if admission.discharge_date and hasattr(admission.discharge_date, "isoformat")
            else str(admission.discharge_date)
        )
        discharge_type_out = admission.discharge_type

        await self.db.commit()

        return {
            "admission_id": admission_id_str,
            "admission_number": admission_number_out,
            "patient_name": patient_name_out,
            "bed_code": bed_code_out,
            "ward_name": ward_name_out,
            "discharge_date": discharge_date_out,
            "discharge_time": discharge_date.strftime("%H:%M") if discharge_date else None,
            "length_of_stay": length_of_stay,
            "discharge_type": discharge_type_out,
            "discharge_summary_id": discharge_summary_id,
            "message": "Patient discharged successfully and bed released",
        }
    
    async def get_admissions(
        self, 
        page: int = 1, 
        limit: int = 50,
        status_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated list of admissions"""
        from app.models.patient import Admission
        from app.models.doctor import DoctorProfile
        from app.models.hospital import Bed, Ward
        
        offset = (page - 1) * limit
        
        # Build query with hospital filter
        query = select(Admission).options(
            selectinload(Admission.patient).selectinload(PatientProfile.user),
            selectinload(Admission.doctor),
            selectinload(Admission.department),
            selectinload(Admission.bed).selectinload(Bed.ward)
        ).where(Admission.hospital_id == self.hospital_id)
        
        # Filter by status (derived from is_active and discharge_date)
        if status_filter:
            if status_filter == "ADMITTED":
                query = query.where(and_(Admission.is_active == True, Admission.discharge_date.is_(None)))
            elif status_filter == "DISCHARGED":
                query = query.where(Admission.discharge_date.isnot(None))
            else:
                query = query.where(Admission.is_active == True)  # fallback
        date_from_d = _parse_iso_date(date_from)
        date_to_d = _parse_iso_date(date_to)

        # Filter by date range (admission_date is timestamptz in PostgreSQL)
        if date_from_d:
            query = query.where(func.date(Admission.admission_date) >= date_from_d)
        if date_to_d:
            query = query.where(func.date(Admission.admission_date) <= date_to_d)

        # Get total count
        count_query = select(func.count(Admission.id)).where(Admission.hospital_id == self.hospital_id)
        if status_filter:
            if status_filter == "ADMITTED":
                count_query = count_query.where(and_(Admission.is_active == True, Admission.discharge_date.is_(None)))
            elif status_filter == "DISCHARGED":
                count_query = count_query.where(Admission.discharge_date.isnot(None))
            else:
                count_query = count_query.where(Admission.is_active == True)
        if date_from_d:
            count_query = count_query.where(func.date(Admission.admission_date) >= date_from_d)
        if date_to_d:
            count_query = count_query.where(func.date(Admission.admission_date) <= date_to_d)
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(Admission.created_at.desc())
        result = await self.db.execute(query)
        admissions = result.scalars().all()
        
        # Format response
        admission_list = []
        for admission in admissions:
            patient_name = f"{admission.patient.user.first_name} {admission.patient.user.last_name}" if admission.patient and admission.patient.user else "Unknown"
            doctor_name = f"{admission.doctor.first_name} {admission.doctor.last_name}" if admission.doctor else "Unknown"
            
            bed_info = None
            if admission.bed:
                bed_info = {
                    "bed_code": admission.bed.bed_code,
                    "bed_number": admission.bed.bed_number,
                    "ward_name": admission.bed.ward.name if admission.bed.ward else None
                }
            
            admission_list.append({
                "id": str(admission.id),
                "admission_number": admission.admission_number,
                "patient_id": str(admission.patient_id),
                "patient_name": patient_name,
                "doctor_id": str(admission.doctor_id),
                "doctor_name": doctor_name,
                "department_name": admission.department.name if admission.department else None,
                "admission_date": admission.admission_date.isoformat() if hasattr(admission.admission_date, "isoformat") else str(admission.admission_date),
                "admission_time": admission.admission_date.strftime("%H:%M") if admission.admission_date and hasattr(admission.admission_date, "strftime") else None,
                "actual_admission_date": getattr(admission, "actual_admission_date", None),
                "discharge_date": admission.discharge_date.isoformat() if admission.discharge_date and hasattr(admission.discharge_date, "isoformat") else admission.discharge_date,
                "length_of_stay": getattr(admission, "length_of_stay", None),
                "status": admission.status,
                "admission_type": admission.admission_type,
                "chief_complaint": admission.chief_complaint,
                "diagnosis": getattr(admission, "diagnosis", None) or admission.provisional_diagnosis,
                "is_emergency": getattr(admission, "is_emergency", False),
                "bed_assignment": bed_info,
                "created_at": admission.created_at.isoformat(),
                "updated_at": admission.updated_at.isoformat()
            })
        
        return {
            "admissions": admission_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }

    # ============================================================================
    # TASK 2.8 - HOSPITAL REPORTS (SOW-ALIGNED)
    # ============================================================================
    
    async def get_bed_occupancy_report(
        self, 
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        ward_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate bed occupancy report"""
        from app.models.hospital import Bed, Ward
        from app.models.patient import Admission
        from app.core.enums import BedStatus, AdmissionStatus
        
        # Set default date range (last 30 days if not specified)
        if not date_from:
            date_from = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
        if not date_to:
            date_to = datetime.utcnow().date().isoformat()
        
        # Get all beds in hospital (optionally filtered by ward)
        beds_query = select(Bed).options(
            selectinload(Bed.ward)
        ).where(Bed.hospital_id == self.hospital_id)
        
        if ward_id:
            beds_query = beds_query.where(Bed.ward_id == uuid.UUID(ward_id))
        
        beds_result = await self.db.execute(beds_query)
        beds = beds_result.scalars().all()
        
        # Calculate current occupancy statistics
        total_beds = len(beds)
        occupied_beds = len([bed for bed in beds if bed.status == BedStatus.OCCUPIED])
        available_beds = len([bed for bed in beds if bed.status == BedStatus.AVAILABLE])
        maintenance_beds = len([bed for bed in beds if bed.status == BedStatus.MAINTENANCE])
        reserved_beds = len([bed for bed in beds if bed.status == BedStatus.RESERVED])
        
        occupancy_rate = round((occupied_beds / total_beds * 100) if total_beds > 0 else 0, 1)
        
        # Get admissions in date range for trend analysis (status derived from is_active and discharge_date)
        df_occ = _parse_iso_date(date_from)
        dt_occ = _parse_iso_date(date_to)
        admissions_query = select(Admission).where(
            and_(
                Admission.hospital_id == self.hospital_id,
                func.date(Admission.admission_date) >= df_occ,
                func.date(Admission.admission_date) <= dt_occ,
                or_(
                    and_(Admission.is_active == True, Admission.discharge_date.is_(None)),
                    Admission.discharge_date.isnot(None),
                ),
            )
        )
        
        if ward_id:
            # Filter by beds in the specific ward (bed_id may be None for some admissions)
            ward_bed_ids = [bed.id for bed in beds]
            admissions_query = admissions_query.where(Admission.bed_id.in_(ward_bed_ids))
        
        admissions_result = await self.db.execute(admissions_query)
        admissions = admissions_result.scalars().all()
        
        # Calculate average length of stay
        discharged_admissions = [adm for adm in admissions if adm.status == AdmissionStatus.DISCHARGED]
        def _length_of_stay(adm):
            los = getattr(adm, "length_of_stay", None)
            if los is not None:
                return los
            if adm.discharge_date and adm.admission_date and hasattr(adm.discharge_date, "__sub__"):
                return (adm.discharge_date - adm.admission_date).days + 1
            return 0
        avg_length_of_stay = round(
            sum(_length_of_stay(adm) for adm in discharged_admissions) / len(discharged_admissions)
            if discharged_admissions else 0, 1
        )
        
        # Ward-wise breakdown
        ward_breakdown = {}
        for bed in beds:
            ward_name = bed.ward.name if bed.ward else "Unassigned"
            if ward_name not in ward_breakdown:
                ward_breakdown[ward_name] = {
                    "ward_id": str(bed.ward_id) if bed.ward_id else None,
                    "total_beds": 0,
                    "occupied": 0,
                    "available": 0,
                    "maintenance": 0,
                    "reserved": 0,
                    "occupancy_rate": 0
                }
            
            ward_breakdown[ward_name]["total_beds"] += 1
            if bed.status == BedStatus.OCCUPIED:
                ward_breakdown[ward_name]["occupied"] += 1
            elif bed.status == BedStatus.AVAILABLE:
                ward_breakdown[ward_name]["available"] += 1
            elif bed.status == BedStatus.MAINTENANCE:
                ward_breakdown[ward_name]["maintenance"] += 1
            elif bed.status == BedStatus.RESERVED:
                ward_breakdown[ward_name]["reserved"] += 1
        
        # Calculate occupancy rates for each ward
        for ward_data in ward_breakdown.values():
            if ward_data["total_beds"] > 0:
                ward_data["occupancy_rate"] = round(
                    (ward_data["occupied"] / ward_data["total_beds"] * 100), 1
                )
        
        # Daily occupancy trend (last 7 days)
        daily_trends = []
        for i in range(7):
            trend_date = (datetime.utcnow() - timedelta(days=i)).date()
            
            # Count admissions on this date
            daily_admissions = len(
                [adm for adm in admissions if _appointment_calendar_day(adm.admission_date) == trend_date]
            )

            daily_discharges = len(
                [
                    adm
                    for adm in admissions
                    if adm.discharge_date and _appointment_calendar_day(adm.discharge_date) == trend_date
                ]
            )
            
            daily_trends.append({
                "date": trend_date.isoformat(),
                "admissions": daily_admissions,
                "discharges": daily_discharges,
                "net_change": daily_admissions - daily_discharges
            })
        
        daily_trends.reverse()  # Show oldest to newest
        
        return {
            "report_type": "bed_occupancy",
            "generated_at": datetime.utcnow().isoformat(),
            "date_range": {
                "from": date_from,
                "to": date_to
            },
            "summary": {
                "total_beds": total_beds,
                "occupied_beds": occupied_beds,
                "available_beds": available_beds,
                "maintenance_beds": maintenance_beds,
                "reserved_beds": reserved_beds,
                "occupancy_rate": occupancy_rate,
                "average_length_of_stay": avg_length_of_stay
            },
            "ward_breakdown": [
                {"ward_name": name, **data} 
                for name, data in ward_breakdown.items()
            ],
            "daily_trends": daily_trends,
            "total_admissions": len([adm for adm in admissions if adm.status in [AdmissionStatus.ADMITTED, AdmissionStatus.DISCHARGED]]),
            "total_discharges": len([adm for adm in admissions if adm.status == AdmissionStatus.DISCHARGED])
        }
    
    async def get_department_performance_report(
        self, 
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate department performance report"""
        from app.models.hospital import Department
        from app.models.patient import Appointment
        from app.models.doctor import DoctorProfile
        
        # Set default date range (last 30 days if not specified)
        if not date_from:
            date_from = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
        if not date_to:
            date_to = datetime.utcnow().date().isoformat()
        
        # Get all departments
        departments_result = await self.db.execute(
            select(Department).options(
                selectinload(Department.head_doctor)
            ).where(Department.hospital_id == self.hospital_id)
        )
        departments = departments_result.scalars().all()
        
        # Get appointments in date range
        appointments_result = await self.db.execute(
            select(Appointment).options(
                selectinload(Appointment.department),
                selectinload(Appointment.doctor)
            ).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    Appointment.appointment_date >= date_from,
                    Appointment.appointment_date <= date_to
                )
            )
        )
        appointments = appointments_result.scalars().all()
        
        # Calculate department performance metrics
        department_performance = []
        
        for department in departments:
            # Filter appointments for this department
            dept_appointments = [
                apt for apt in appointments 
                if apt.department_id == department.id
            ]
            
            # Calculate metrics
            total_appointments = len(dept_appointments)
            completed_appointments = len([
                apt for apt in dept_appointments 
                if apt.status == "COMPLETED"
            ])
            cancelled_appointments = len([
                apt for apt in dept_appointments 
                if apt.status == "CANCELLED"
            ])
            no_show_appointments = len([
                apt for apt in dept_appointments 
                if apt.status == "NO_SHOW"
            ])
            
            completion_rate = round(
                (completed_appointments / total_appointments * 100) 
                if total_appointments > 0 else 0, 1
            )
            
            cancellation_rate = round(
                (cancelled_appointments / total_appointments * 100) 
                if total_appointments > 0 else 0, 1
            )
            
            no_show_rate = round(
                (no_show_appointments / total_appointments * 100) 
                if total_appointments > 0 else 0, 1
            )
            
            # Count active doctors in department
            doctors_result = await self.db.execute(
                select(func.count(DoctorProfile.id)).where(
                    and_(
                        DoctorProfile.department_id == department.id,
                        DoctorProfile.hospital_id == self.hospital_id
                    )
                )
            )
            doctor_count = doctors_result.scalar() or 0
            
            # Calculate revenue (sum of consultation fees)
            total_revenue = sum([
                float(apt.consultation_fee) for apt in dept_appointments 
                if apt.consultation_fee and apt.status == "COMPLETED"
            ])
            
            # Average appointments per doctor
            avg_appointments_per_doctor = round(
                (total_appointments / doctor_count) if doctor_count > 0 else 0, 1
            )
            
            head_doctor_name = None
            if department.head_doctor:
                head_doctor_name = f"{department.head_doctor.first_name} {department.head_doctor.last_name}"
            
            department_performance.append({
                "department_id": str(department.id),
                "department_name": department.name,
                "department_code": department.code,
                "head_doctor": head_doctor_name,
                "doctor_count": doctor_count,
                "metrics": {
                    "total_appointments": total_appointments,
                    "completed_appointments": completed_appointments,
                    "cancelled_appointments": cancelled_appointments,
                    "no_show_appointments": no_show_appointments,
                    "completion_rate": completion_rate,
                    "cancellation_rate": cancellation_rate,
                    "no_show_rate": no_show_rate,
                    "avg_appointments_per_doctor": avg_appointments_per_doctor
                },
                "revenue": {
                    "total_revenue": total_revenue,
                    "avg_revenue_per_appointment": round(
                        (total_revenue / completed_appointments) 
                        if completed_appointments > 0 else 0, 2
                    )
                }
            })
        
        # Sort by total appointments (most active first)
        department_performance.sort(key=lambda x: x["metrics"]["total_appointments"], reverse=True)
        
        # Calculate hospital-wide totals
        total_hospital_appointments = sum([dept["metrics"]["total_appointments"] for dept in department_performance])
        total_hospital_revenue = sum([dept["revenue"]["total_revenue"] for dept in department_performance])
        total_doctors = sum([dept["doctor_count"] for dept in department_performance])
        
        return {
            "report_type": "department_performance",
            "generated_at": datetime.utcnow().isoformat(),
            "date_range": {
                "from": date_from,
                "to": date_to
            },
            "hospital_summary": {
                "total_departments": len(departments),
                "total_doctors": total_doctors,
                "total_appointments": total_hospital_appointments,
                "total_revenue": total_hospital_revenue,
                "avg_appointments_per_department": round(
                    (total_hospital_appointments / len(departments)) 
                    if len(departments) > 0 else 0, 1
                )
            },
            "department_performance": department_performance
        }
    
    async def get_revenue_summary_report(
        self, 
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate revenue summary report - DISABLED: Billing module removed"""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"code": "BILLING_REMOVED", "message": "Revenue summary report requires billing module which has been removed"}
        )
        
        # from app.models.patient import Appointment
        # from app.models.hospital import Department
        
        # Set default date range (last 30 days if not specified)
        if not date_from:
            date_from = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
        if not date_to:
            date_to = datetime.utcnow().date().isoformat()
        
        # Get appointments in date range
        appointments_result = await self.db.execute(
            select(Appointment).options(
                selectinload(Appointment.department)
            ).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    Appointment.appointment_date >= date_from,
                    Appointment.appointment_date <= date_to,
                    Appointment.status == "COMPLETED"
                )
            )
        )
        appointments = appointments_result.scalars().all()
        
        # Get invoices in date range
        invoices_result = await self.db.execute(
            select(Invoice).where(
                and_(
                    Invoice.hospital_id == self.hospital_id,
                    Invoice.invoice_date >= date_from,
                    Invoice.invoice_date <= date_to
                )
            )
        )
        invoices = invoices_result.scalars().all()
        
        # Get payments in date range
        payments_result = await self.db.execute(
            select(Payment).where(
                and_(
                    Payment.hospital_id == self.hospital_id,
                    Payment.payment_date >= date_from,
                    Payment.payment_date <= date_to,
                    Payment.status == "COMPLETED"
                )
            )
        )
        payments = payments_result.scalars().all()
        
        # Calculate consultation revenue
        consultation_revenue = sum([
            float(apt.consultation_fee) for apt in appointments 
            if apt.consultation_fee
        ])
        
        # Calculate invoice totals
        total_invoiced = sum([float(inv.total_amount) for inv in invoices])
        total_paid = sum([float(pay.amount) for pay in payments])
        outstanding_amount = total_invoiced - total_paid
        
        # Revenue by department
        department_revenue = {}
        for appointment in appointments:
            if appointment.department and appointment.consultation_fee:
                dept_name = appointment.department.name
                if dept_name not in department_revenue:
                    department_revenue[dept_name] = {
                        "department_id": str(appointment.department_id),
                        "appointment_count": 0,
                        "revenue": 0
                    }
                department_revenue[dept_name]["appointment_count"] += 1
                department_revenue[dept_name]["revenue"] += float(appointment.consultation_fee)
        
        # Convert to list and sort by revenue
        department_revenue_list = [
            {"department_name": name, **data} 
            for name, data in department_revenue.items()
        ]
        department_revenue_list.sort(key=lambda x: x["revenue"], reverse=True)
        
        # Daily revenue trend (last 7 days)
        daily_revenue = []
        for i in range(7):
            trend_date = (datetime.utcnow() - timedelta(days=i)).date().isoformat()
            
            daily_appointments = [
                apt for apt in appointments 
                if apt.appointment_date == trend_date
            ]
            
            daily_payments = [
                pay for pay in payments 
                if pay.payment_date == trend_date
            ]
            
            daily_consultation_revenue = sum([
                float(apt.consultation_fee) for apt in daily_appointments 
                if apt.consultation_fee
            ])
            
            daily_payment_revenue = sum([
                float(pay.amount) for pay in daily_payments
            ])
            
            daily_revenue.append({
                "date": trend_date,
                "consultation_revenue": daily_consultation_revenue,
                "payment_revenue": daily_payment_revenue,
                "total_revenue": daily_consultation_revenue + daily_payment_revenue,
                "appointment_count": len(daily_appointments)
            })
        
        daily_revenue.reverse()  # Show oldest to newest
        
        # Payment method breakdown
        payment_methods = {}
        for payment in payments:
            method = payment.payment_method or "Unknown"
            if method not in payment_methods:
                payment_methods[method] = {"count": 0, "amount": 0}
            payment_methods[method]["count"] += 1
            payment_methods[method]["amount"] += float(payment.amount)
        
        payment_method_list = [
            {"method": method, **data} 
            for method, data in payment_methods.items()
        ]
        payment_method_list.sort(key=lambda x: x["amount"], reverse=True)
        
        return {
            "report_type": "revenue_summary",
            "generated_at": datetime.utcnow().isoformat(),
            "date_range": {
                "from": date_from,
                "to": date_to
            },
            "revenue_summary": {
                "consultation_revenue": consultation_revenue,
                "total_invoiced": total_invoiced,
                "total_paid": total_paid,
                "outstanding_amount": outstanding_amount,
                "collection_rate": round(
                    (total_paid / total_invoiced * 100) 
                    if total_invoiced > 0 else 0, 1
                )
            },
            "department_revenue": department_revenue_list,
            "daily_revenue_trend": daily_revenue,
            "payment_methods": payment_method_list,
            "statistics": {
                "total_appointments": len(appointments),
                "total_invoices": len(invoices),
                "total_payments": len(payments),
                "avg_consultation_fee": round(
                    (consultation_revenue / len(appointments)) 
                    if len(appointments) > 0 else 0, 2
                ),
                "avg_invoice_amount": round(
                    (total_invoiced / len(invoices)) 
                    if len(invoices) > 0 else 0, 2
                )
            }
        }

    # ============================================================================
    # TASK 2.9 - HOSPITAL DASHBOARD & REPORTS
    # ============================================================================
    
    async def get_dashboard_overview(self) -> Dict[str, Any]:
        """Get hospital dashboard overview with key metrics"""
        from app.models.hospital import Department, Bed, Ward
        from app.models.patient import Appointment, Admission, PatientProfile
        from app.models.doctor import DoctorProfile
        from app.models.user import User
        from app.models.payments.payment import Payment
        from app.core.enums import BedStatus

        # Calendar dates (PostgreSQL: compare via DATE(...) — avoids timestamptz = varchar errors)
        today_d = datetime.utcnow().date()
        this_month_start_d = datetime.utcnow().replace(day=1).date()
        thirty_days_ago_d = today_d - timedelta(days=30)

        # === PATIENT METRICS ===
        # Total patients
        total_patients_result = await self.db.execute(
            select(func.count(PatientProfile.id)).where(
                PatientProfile.hospital_id == self.hospital_id
            )
        )
        total_patients = total_patients_result.scalar() or 0
        
        # Active patients (with appointments in last 30 days)
        active_patients_result = await self.db.execute(
            select(func.count(func.distinct(Appointment.patient_id))).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    func.date(Appointment.appointment_date) >= thirty_days_ago_d,
                )
            )
        )
        active_patients = active_patients_result.scalar() or 0
        
        # === STAFF METRICS ===
        # Total doctors
        total_doctors_result = await self.db.execute(
            select(func.count(DoctorProfile.id)).where(
                DoctorProfile.hospital_id == self.hospital_id
            )
        )
        total_doctors = total_doctors_result.scalar() or 0
        
        # Active doctors (with appointments in last 30 days)
        active_doctors_result = await self.db.execute(
            select(func.count(func.distinct(Appointment.doctor_id))).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    func.date(Appointment.appointment_date) >= thirty_days_ago_d,
                )
            )
        )
        active_doctors = active_doctors_result.scalar() or 0
        
        # Total staff (all non-patient users)
        total_staff_result = await self.db.execute(
            select(func.count(User.id)).where(
                and_(
                    User.hospital_id == self.hospital_id,
                    User.roles.any(
                        Role.name.in_(
                            [
                                UserRole.DOCTOR.value,
                                UserRole.LAB_TECH.value,
                                UserRole.PHARMACIST.value,
                                UserRole.HOSPITAL_ADMIN.value,
                            ]
                        )
                    )
                )
            )
        )
        total_staff = total_staff_result.scalar() or 0
        
        # === APPOINTMENT METRICS ===
        # Today's appointments
        todays_appointments_result = await self.db.execute(
            select(func.count(Appointment.id)).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    func.date(Appointment.appointment_date) == today_d,
                )
            )
        )
        todays_appointments = todays_appointments_result.scalar() or 0
        
        # This month's appointments
        monthly_appointments_result = await self.db.execute(
            select(func.count(Appointment.id)).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    func.date(Appointment.appointment_date) >= this_month_start_d,
                )
            )
        )
        monthly_appointments = monthly_appointments_result.scalar() or 0
        
        # Completed appointments this month
        completed_appointments_result = await self.db.execute(
            select(func.count(Appointment.id)).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    func.date(Appointment.appointment_date) >= this_month_start_d,
                    Appointment.status == "COMPLETED",
                )
            )
        )
        completed_appointments = completed_appointments_result.scalar() or 0
        
        # === BED & ADMISSION METRICS ===
        # Total beds
        total_beds_result = await self.db.execute(
            select(func.count(Bed.id)).where(Bed.hospital_id == self.hospital_id)
        )
        total_beds = total_beds_result.scalar() or 0
        
        # Occupied beds
        occupied_beds_result = await self.db.execute(
            select(func.count(Bed.id)).where(
                and_(
                    Bed.hospital_id == self.hospital_id,
                    Bed.status == BedStatus.OCCUPIED
                )
            )
        )
        occupied_beds = occupied_beds_result.scalar() or 0
        
        # Current admissions (status derived: is_active and no discharge_date)
        current_admissions_result = await self.db.execute(
            select(func.count(Admission.id)).where(
                and_(
                    Admission.hospital_id == self.hospital_id,
                    Admission.is_active == True,
                    Admission.discharge_date.is_(None),
                )
            )
        )
        current_admissions = current_admissions_result.scalar() or 0
        
        # Today's admissions
        todays_admissions_result = await self.db.execute(
            select(func.count(Admission.id)).where(
                and_(
                    Admission.hospital_id == self.hospital_id,
                    func.date(Admission.admission_date) == today_d,
                )
            )
        )
        todays_admissions = todays_admissions_result.scalar() or 0
        
        # Today's discharges
        todays_discharges_result = await self.db.execute(
            select(func.count(Admission.id)).where(
                and_(
                    Admission.hospital_id == self.hospital_id,
                    func.date(Admission.discharge_date) == today_d,
                )
            )
        )
        todays_discharges = todays_discharges_result.scalar() or 0
        
        # === DEPARTMENT METRICS ===
        total_departments_result = await self.db.execute(
            select(func.count(Department.id)).where(
                Department.hospital_id == self.hospital_id
            )
        )
        total_departments = total_departments_result.scalar() or 0
        
        total_wards_result = await self.db.execute(
            select(func.count(Ward.id)).where(Ward.hospital_id == self.hospital_id)
        )
        total_wards = total_wards_result.scalar() or 0
        
        # === REVENUE METRICS ===
        # This month's revenue from appointments
        monthly_consultation_revenue_result = await self.db.execute(
            select(func.coalesce(func.sum(Appointment.consultation_fee), 0)).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    func.date(Appointment.appointment_date) >= this_month_start_d,
                    Appointment.status == "COMPLETED",
                )
            )
        )
        monthly_consultation_revenue = float(monthly_consultation_revenue_result.scalar() or 0)
        
        # This month's payments
        monthly_payments_result = await self.db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                and_(
                    Payment.hospital_id == self.hospital_id,
                    Payment.paid_at.isnot(None),
                    func.date(Payment.paid_at) >= this_month_start_d,
                    Payment.status == "SUCCESS",
                )
            )
        )
        monthly_payments = float(monthly_payments_result.scalar() or 0)
        
        # Calculate rates and percentages
        bed_occupancy_rate = round((occupied_beds / total_beds * 100) if total_beds > 0 else 0, 1)
        appointment_completion_rate = round((completed_appointments / monthly_appointments * 100) if monthly_appointments > 0 else 0, 1)
        doctor_utilization_rate = round((active_doctors / total_doctors * 100) if total_doctors > 0 else 0, 1)
        
        # Recent activity (last 7 days trend)
        recent_activity = []
        for i in range(7):
            activity_d = (datetime.utcnow() - timedelta(days=i)).date()

            daily_appointments_result = await self.db.execute(
                select(func.count(Appointment.id)).where(
                    and_(
                        Appointment.hospital_id == self.hospital_id,
                        func.date(Appointment.appointment_date) == activity_d,
                    )
                )
            )
            daily_appointments = daily_appointments_result.scalar() or 0
            
            daily_admissions_result = await self.db.execute(
                select(func.count(Admission.id)).where(
                    and_(
                        Admission.hospital_id == self.hospital_id,
                        func.date(Admission.admission_date) == activity_d,
                    )
                )
            )
            daily_admissions = daily_admissions_result.scalar() or 0
            
            recent_activity.append(
                {
                    "date": activity_d.isoformat(),
                    "appointments": daily_appointments,
                    "admissions": daily_admissions,
                }
            )
        
        recent_activity.reverse()  # Show oldest to newest
        
        return {
            "dashboard_type": "overview",
            "generated_at": datetime.utcnow().isoformat(),
            "hospital_id": str(self.hospital_id),
            "patient_metrics": {
                "total_patients": total_patients,
                "active_patients": active_patients,
                "patient_activity_rate": round((active_patients / total_patients * 100) if total_patients > 0 else 0, 1)
            },
            "staff_metrics": {
                "total_staff": total_staff,
                "total_doctors": total_doctors,
                "active_doctors": active_doctors,
                "doctor_utilization_rate": doctor_utilization_rate
            },
            "appointment_metrics": {
                "todays_appointments": todays_appointments,
                "monthly_appointments": monthly_appointments,
                "completed_appointments": completed_appointments,
                "appointment_completion_rate": appointment_completion_rate
            },
            "bed_metrics": {
                "total_beds": total_beds,
                "occupied_beds": occupied_beds,
                "available_beds": total_beds - occupied_beds,
                "bed_occupancy_rate": bed_occupancy_rate,
                "current_admissions": current_admissions,
                "todays_admissions": todays_admissions,
                "todays_discharges": todays_discharges
            },
            "facility_metrics": {
                "total_departments": total_departments,
                "total_wards": total_wards
            },
            "revenue_metrics": {
                "monthly_consultation_revenue": monthly_consultation_revenue,
                "monthly_payments": monthly_payments,
                "total_monthly_revenue": monthly_consultation_revenue + monthly_payments
            },
            "recent_activity": recent_activity
        }
    
    async def get_staff_statistics(self) -> Dict[str, Any]:
        """Get detailed staff statistics"""
        from app.models.doctor import DoctorProfile
        from app.models.hospital import Department
        from app.models.user import User
        from app.models.patient import Appointment
        
        # Get all staff users
        staff_result = await self.db.execute(
            select(User).options(selectinload(User.roles)).where(
                and_(
                    User.hospital_id == self.hospital_id,
                    User.roles.any(
                        Role.name.in_(
                            [
                                UserRole.DOCTOR.value,
                                UserRole.LAB_TECH.value,
                                UserRole.PHARMACIST.value,
                                UserRole.HOSPITAL_ADMIN.value,
                            ]
                        )
                    )
                )
            )
        )
        staff_users = staff_result.scalars().all()
        
        # Categorize staff by role
        staff_by_role = {
            UserRole.DOCTOR.value: [],
            UserRole.LAB_TECH.value: [],
            UserRole.PHARMACIST.value: [],
            UserRole.HOSPITAL_ADMIN.value: [],
        }
        
        for user in staff_users:
            user_roles = [role.name for role in user.roles]
            for role in user_roles:
                if role in staff_by_role:
                    staff_by_role[role].append(user)
        
        # Get doctor profiles with department info
        doctors_result = await self.db.execute(
            select(DoctorProfile).options(
                selectinload(DoctorProfile.user),
                selectinload(DoctorProfile.department)
            ).where(DoctorProfile.hospital_id == self.hospital_id)
        )
        doctors = doctors_result.scalars().all()
        
        # Get departments
        departments_result = await self.db.execute(
            select(Department).options(
                selectinload(Department.head_doctor)
            ).where(Department.hospital_id == self.hospital_id)
        )
        departments = departments_result.scalars().all()
        
        # Calculate doctor statistics
        doctor_stats = []
        thirty_days_ago_d = (datetime.utcnow() - timedelta(days=30)).date()

        for doctor in doctors:
            # Get appointment count for last 30 days
            appointments_result = await self.db.execute(
                select(func.count(Appointment.id)).where(
                    and_(
                        Appointment.doctor_id == doctor.user_id,
                        func.date(Appointment.appointment_date) >= thirty_days_ago_d,
                    )
                )
            )
            appointment_count = appointments_result.scalar() or 0
            
            # Get completed appointments
            completed_result = await self.db.execute(
                select(func.count(Appointment.id)).where(
                    and_(
                        Appointment.doctor_id == doctor.user_id,
                        func.date(Appointment.appointment_date) >= thirty_days_ago_d,
                        Appointment.status == "COMPLETED",
                    )
                )
            )
            completed_count = completed_result.scalar() or 0
            
            completion_rate = round((completed_count / appointment_count * 100) if appointment_count > 0 else 0, 1)
            
            doctor_stats.append({
                "doctor_id": str(doctor.id),
                "name": f"{doctor.user.first_name} {doctor.user.last_name}",
                "specialization": doctor.specialization,
                "department": doctor.department.name if doctor.department else "Unassigned",
                "experience_years": doctor.experience_years,
                "is_active": doctor.user.is_active,
                "last_30_days": {
                    "total_appointments": appointment_count,
                    "completed_appointments": completed_count,
                    "completion_rate": completion_rate
                }
            })
        
        # Sort doctors by appointment count
        doctor_stats.sort(key=lambda x: x["last_30_days"]["total_appointments"], reverse=True)
        
        # Department-wise staff distribution
        department_staff = []
        for department in departments:
            dept_doctors = [d for d in doctors if d.department_id == department.id]
            
            department_staff.append({
                "department_id": str(department.id),
                "department_name": department.name,
                "head_doctor": f"{department.head_doctor.first_name} {department.head_doctor.last_name}" if department.head_doctor else None,
                "doctor_count": len(dept_doctors),
                "is_active": department.is_active
            })
        
        # Staff summary by role
        role_summary = []
        for role, users in staff_by_role.items():
            active_count = len([u for u in users if u.is_active])
            inactive_count = len(users) - active_count
            
            role_summary.append({
                "role": role,
                "total_count": len(users),
                "active_count": active_count,
                "inactive_count": inactive_count
            })
        
        return {
            "report_type": "staff_statistics",
            "generated_at": datetime.utcnow().isoformat(),
            "hospital_id": str(self.hospital_id),
            "summary": {
                "total_staff": len(staff_users),
                "active_staff": len([u for u in staff_users if u.is_active]),
                "total_doctors": len(doctors),
                "total_departments": len(departments)
            },
            "role_breakdown": role_summary,
            "doctor_performance": doctor_stats[:10],  # Top 10 doctors by activity
            "department_distribution": department_staff
        }
    
    async def get_appointment_statistics(self) -> Dict[str, Any]:
        """Get detailed appointment statistics"""
        from app.models.patient import Appointment
        from app.models.hospital import Department
        from app.models.doctor import DoctorProfile
        
        # Date ranges
        today_d = datetime.utcnow().date()
        this_week_start_d = (datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())).date()
        this_month_start_d = datetime.utcnow().replace(day=1).date()
        last_30_d = (datetime.utcnow() - timedelta(days=30)).date()

        # Get all appointments for analysis
        appointments_result = await self.db.execute(
            select(Appointment).options(
                selectinload(Appointment.department),
                selectinload(Appointment.doctor)
            ).where(
                and_(
                    Appointment.hospital_id == self.hospital_id,
                    func.date(Appointment.appointment_date) >= last_30_d,
                )
            )
        )
        appointments = appointments_result.scalars().all()
        
        # Overall statistics
        total_appointments = len(appointments)
        completed_appointments = len([a for a in appointments if a.status == "COMPLETED"])
        cancelled_appointments = len([a for a in appointments if a.status == "CANCELLED"])
        no_show_appointments = len([a for a in appointments if a.status == "NO_SHOW"])
        pending_appointments = len([a for a in appointments if a.status in ["SCHEDULED", "CONFIRMED"]])
        
        # Calculate rates
        completion_rate = round((completed_appointments / total_appointments * 100) if total_appointments > 0 else 0, 1)
        cancellation_rate = round((cancelled_appointments / total_appointments * 100) if total_appointments > 0 else 0, 1)
        no_show_rate = round((no_show_appointments / total_appointments * 100) if total_appointments > 0 else 0, 1)
        
        # Today's appointments
        todays_appointments = [a for a in appointments if _appointment_calendar_day(a.appointment_date) == today_d]

        # This week's appointments
        weekly_appointments = [
            a for a in appointments if _appointment_calendar_day(a.appointment_date) >= this_week_start_d
        ]

        # This month's appointments
        monthly_appointments = [
            a for a in appointments if _appointment_calendar_day(a.appointment_date) >= this_month_start_d
        ]
        
        # Department-wise breakdown
        department_stats = {}
        for appointment in appointments:
            if appointment.department:
                dept_name = appointment.department.name
                if dept_name not in department_stats:
                    department_stats[dept_name] = {
                        "department_id": str(appointment.department_id),
                        "total": 0,
                        "completed": 0,
                        "cancelled": 0,
                        "no_show": 0,
                        "revenue": 0
                    }
                
                department_stats[dept_name]["total"] += 1
                if appointment.status == "COMPLETED":
                    department_stats[dept_name]["completed"] += 1
                    if appointment.consultation_fee:
                        department_stats[dept_name]["revenue"] += float(appointment.consultation_fee)
                elif appointment.status == "CANCELLED":
                    department_stats[dept_name]["cancelled"] += 1
                elif appointment.status == "NO_SHOW":
                    department_stats[dept_name]["no_show"] += 1
        
        # Convert to list and add completion rates
        department_breakdown = []
        for dept_name, stats in department_stats.items():
            completion_rate = round((stats["completed"] / stats["total"] * 100) if stats["total"] > 0 else 0, 1)
            department_breakdown.append({
                "department_name": dept_name,
                "department_id": stats["department_id"],
                "total_appointments": stats["total"],
                "completed_appointments": stats["completed"],
                "cancelled_appointments": stats["cancelled"],
                "no_show_appointments": stats["no_show"],
                "completion_rate": completion_rate,
                "revenue": stats["revenue"]
            })
        
        # Sort by total appointments
        department_breakdown.sort(key=lambda x: x["total_appointments"], reverse=True)
        
        # Daily trend (last 7 days)
        daily_trends = []
        for i in range(7):
            trend_d = (datetime.utcnow() - timedelta(days=i)).date()
            daily_appointments = [a for a in appointments if _appointment_calendar_day(a.appointment_date) == trend_d]
            
            daily_trends.append(
                {
                    "date": trend_d.isoformat(),
                    "total_appointments": len(daily_appointments),
                    "completed": len([a for a in daily_appointments if a.status == "COMPLETED"]),
                    "cancelled": len([a for a in daily_appointments if a.status == "CANCELLED"]),
                    "no_show": len([a for a in daily_appointments if a.status == "NO_SHOW"]),
                }
            )
        
        daily_trends.reverse()  # Show oldest to newest
        
        # Appointment type breakdown
        type_breakdown = {}
        for appointment in appointments:
            apt_type = appointment.appointment_type or "REGULAR"
            if apt_type not in type_breakdown:
                type_breakdown[apt_type] = 0
            type_breakdown[apt_type] += 1
        
        type_breakdown_list = [
            {"type": apt_type, "count": count} 
            for apt_type, count in type_breakdown.items()
        ]
        type_breakdown_list.sort(key=lambda x: x["count"], reverse=True)
        
        # Emergency appointments
        emergency_appointments = len([a for a in appointments if _appointment_is_emergency(a)])
        
        return {
            "report_type": "appointment_statistics",
            "generated_at": datetime.utcnow().isoformat(),
            "hospital_id": str(self.hospital_id),
            "date_range": {
                "from": last_30_d.isoformat(),
                "to": today_d.isoformat(),
            },
            "overall_statistics": {
                "total_appointments": total_appointments,
                "completed_appointments": completed_appointments,
                "cancelled_appointments": cancelled_appointments,
                "no_show_appointments": no_show_appointments,
                "pending_appointments": pending_appointments,
                "emergency_appointments": emergency_appointments,
                "completion_rate": completion_rate,
                "cancellation_rate": cancellation_rate,
                "no_show_rate": no_show_rate
            },
            "time_period_breakdown": {
                "today": {
                    "total": len(todays_appointments),
                    "completed": len([a for a in todays_appointments if a.status == "COMPLETED"])
                },
                "this_week": {
                    "total": len(weekly_appointments),
                    "completed": len([a for a in weekly_appointments if a.status == "COMPLETED"])
                },
                "this_month": {
                    "total": len(monthly_appointments),
                    "completed": len([a for a in monthly_appointments if a.status == "COMPLETED"])
                }
            },
            "department_breakdown": department_breakdown,
            "daily_trends": daily_trends,
            "appointment_types": type_breakdown_list
        }

    # ============================================================================
    # HELPER METHODS
    # ============================================================================
    
    async def _get_hospital_doctor(self, doctor_id: uuid.UUID) -> Optional[User]:
        """Get doctor by ID within this hospital"""
        query = select(User).options(selectinload(User.roles)).where(
            and_(
                User.id == doctor_id,
                User.hospital_id == self.hospital_id,
                User.roles.any(Role.name == UserRole.DOCTOR.value)
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def _get_hospital_doctor_by_name(self, doctor_name: str) -> Optional[User]:
        """Get doctor by name within this hospital"""
        # Clean the name and remove common titles
        cleaned_name = doctor_name.strip()
        
        # Remove common titles
        titles = ["Dr.", "Dr", "Doctor", "Prof.", "Prof", "Professor"]
        for title in titles:
            if cleaned_name.startswith(title + " "):
                cleaned_name = cleaned_name[len(title):].strip()
            elif cleaned_name.startswith(title + "."):
                cleaned_name = cleaned_name[len(title) + 1:].strip()
        
        # Split the cleaned name
        name_parts = cleaned_name.split()
        if len(name_parts) < 2:
            return None
        
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:])  # Handle multiple last names
        
        # Try exact match first (but never crash if multiple matches exist).
        query = select(User).options(selectinload(User.roles)).where(
            and_(
                User.hospital_id == self.hospital_id,
                User.roles.any(Role.name == UserRole.DOCTOR.value),
                User.first_name.ilike(first_name),
                User.last_name.ilike(last_name)
            )
        ).limit(1)
        result = await self.db.execute(query)
        doctor = result.scalars().first()
        
        if doctor:
            return doctor
        
        # If exact match fails, try partial match (limit to one deterministic row).
        query = select(User).options(selectinload(User.roles)).where(
            and_(
                User.hospital_id == self.hospital_id,
                User.roles.any(Role.name == UserRole.DOCTOR.value),
                User.first_name.ilike(f"%{first_name}%"),
                User.last_name.ilike(f"%{last_name}%")
            )
        ).limit(1)
        result = await self.db.execute(query)
        return result.scalars().first()
    
    async def _get_hospital_department(self, department_id: uuid.UUID) -> Optional[Department]:
        """Get department by ID within this hospital"""
        query = select(Department).where(
            and_(
                Department.id == department_id,
                Department.hospital_id == self.hospital_id
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def _get_hospital_doctor_profile(self, doctor_profile_id: uuid.UUID) -> Optional['DoctorProfile']:
        """Get doctor profile by ID within this hospital"""
        from app.models.doctor import DoctorProfile
        query = select(DoctorProfile).where(
            and_(
                DoctorProfile.id == doctor_profile_id,
                DoctorProfile.hospital_id == self.hospital_id
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def _get_hospital_doctor_by_ref_or_name(self, ref_or_name: str) -> Optional['DoctorProfile']:
        """Resolve doctor ref (DOC-xxx, UUID) or doctor name to DoctorProfile. Returns profile with .user_id for appointment.doctor_id."""
        from app.models.doctor import DoctorProfile
        ref = (ref_or_name or "").strip()
        if not ref:
            return None
        # 1) Try as UUID (DoctorProfile.id or User.id)
        try:
            uid = uuid.UUID(ref)
            q = select(DoctorProfile).where(
                and_(
                    DoctorProfile.hospital_id == self.hospital_id,
                    (DoctorProfile.id == uid) | (DoctorProfile.user_id == uid)
                )
            ).options(selectinload(DoctorProfile.user)).limit(1)
            r = await self.db.execute(q)
            return r.scalar_one_or_none()
        except ValueError:
            pass
        # 2) Try doctor_id (ref string e.g. DOC-xxx)
        q = select(DoctorProfile).where(
            and_(
                DoctorProfile.hospital_id == self.hospital_id,
                DoctorProfile.doctor_id == ref
            )
        ).options(selectinload(DoctorProfile.user)).limit(1)
        r = await self.db.execute(q)
        doc = r.scalar_one_or_none()
        if doc:
            return doc
        # 3) Try name match (User first_name / last_name)
        name_part = f"%{ref}%"
        q = (
            select(DoctorProfile)
            .join(User, DoctorProfile.user_id == User.id)
            .where(
                and_(
                    DoctorProfile.hospital_id == self.hospital_id,
                    or_(
                        User.first_name.ilike(name_part),
                        User.last_name.ilike(name_part),
                        func.concat(User.first_name, " ", User.last_name).ilike(name_part),
                    )
                )
            )
            .options(selectinload(DoctorProfile.user))
            .limit(1)
        )
        r = await self.db.execute(q)
        return r.scalar_one_or_none()
    
    async def _get_hospital_staff_user(self, user_id: uuid.UUID) -> Optional[User]:
        """Load a user by ID that belongs to this hospital (caller validates role)."""
        query = select(User).options(selectinload(User.roles)).where(
            and_(User.id == user_id, User.hospital_id == self.hospital_id)
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def _get_hospital_patient(self, patient_id: uuid.UUID) -> Optional['PatientProfile']:
        """Get patient by ID within this hospital"""
        from app.models.patient import PatientProfile
        query = select(PatientProfile).where(
            and_(
                PatientProfile.id == patient_id,
                PatientProfile.hospital_id == self.hospital_id
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def _resolve_patient_profile_uuid(self, raw: Any) -> uuid.UUID:
        """Resolve ``patient_profiles.id`` from a UUID or hospital patient ref (e.g. PAT-001)."""
        from app.models.patient import PatientProfile

        if isinstance(raw, uuid.UUID):
            patient = await self._get_hospital_patient(raw)
            if not patient:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "PATIENT_NOT_FOUND",
                        "message": "Patient not found in this hospital",
                    },
                )
            return patient.id
        s = str(raw).strip()
        if not s:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "MISSING_PATIENT_ID", "message": "patient_id is required"},
            )
        try:
            uid = uuid.UUID(s)
        except ValueError:
            uid = None
        if uid is not None:
            patient = await self._get_hospital_patient(uid)
            if not patient:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "PATIENT_NOT_FOUND",
                        "message": "Patient not found in this hospital",
                    },
                )
            return patient.id
        pr = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.hospital_id == self.hospital_id,
                    PatientProfile.patient_id == s,
                )
            )
            .limit(1)
        )
        patient = pr.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "PATIENT_NOT_FOUND",
                    "message": f"Patient not found in this hospital: {s}",
                },
            )
        return patient.id
    
    async def _verify_hospital_admin_access(self, user: User) -> None:
        """Verify user has Hospital Admin access for this hospital"""
        user_roles = [role.name for role in user.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "INSUFFICIENT_PERMISSIONS", "message": "Hospital Admin access required"}
            )
        
        if user.hospital_id != self.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "CROSS_HOSPITAL_ACCESS", "message": "Access to other hospitals is forbidden"}
            )
    
    # ============================================================================
    # DEPARTMENT ASSIGNMENT METHODS
    # ============================================================================
    
    async def assign_staff_to_department(self, assignment_data: Dict[str, Any]) -> Dict[str, Any]:
        """Assign staff member to a department"""
        staff_name = assignment_data['staff_name']
        department_name = assignment_data['department_name']
        
        # Find staff member by name
        staff_member = await self._get_staff_by_name(staff_name)
        if not staff_member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "STAFF_NOT_FOUND", "message": f"Staff member '{staff_name}' not found in this hospital"}
            )

        staff_roles = [role.name for role in staff_member.roles]

        # Find department by name
        department = await self._get_department_by_name(department_name)
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DEPARTMENT_NOT_FOUND", "message": f"Department '{department_name}' not found in this hospital"}
            )
        
        # Check if assignment already exists
        from app.models.hospital import StaffDepartmentAssignment, StaffProfile
        existing_assignment = await self.db.execute(
            select(StaffDepartmentAssignment).where(
                and_(
                    StaffDepartmentAssignment.staff_id == staff_member.id,
                    StaffDepartmentAssignment.department_id == department.id,
                    StaffDepartmentAssignment.is_active == True
                )
            )
        )
        
        if existing_assignment.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "ASSIGNMENT_EXISTS", "message": f"Staff member is already assigned to {department_name}"}
            )
        
        # Create assignment
        from datetime import datetime
        from app.core.utils import parse_date_string
        from app.services.super_admin_service import generate_staff_id
        
        effective_from = parse_date_string(assignment_data.get('effective_from')) or datetime.utcnow()
        
        is_primary = assignment_data.get('is_primary', True)

        # Update staff ID with actual department name if this is primary assignment
        if is_primary:
            staff_role = None
            for role in [UserRole.DOCTOR, UserRole.NURSE, UserRole.RECEPTIONIST, UserRole.PHARMACIST, UserRole.LAB_TECH]:
                if role in staff_roles:
                    staff_role = role
                    break
            
            if staff_role:
                # Generate new staff ID with actual department
                new_staff_id = generate_staff_id(
                    role=staff_role,
                    department_name=department.name,
                    first_name=staff_member.first_name,
                    last_name=staff_member.last_name
                )
                
                # Ensure uniqueness
                existing_staff_id = await self.db.execute(
                    select(User).where(
                        and_(
                            User.staff_id == new_staff_id,
                            User.id != staff_member.id
                        )
                    )
                )
                counter = 1
                original_staff_id = new_staff_id
                while existing_staff_id.scalar_one_or_none():
                    new_staff_id = original_staff_id[:-2] + f"{counter:02d}"
                    existing_staff_id = await self.db.execute(
                        select(User).where(
                            and_(
                                User.staff_id == new_staff_id,
                                User.id != staff_member.id
                            )
                        )
                    )
                    counter += 1
                    if counter > 99:
                        import random
                        new_staff_id = original_staff_id[:-2] + f"{random.randint(10, 99)}"
                        break
                
                # Update staff member's staff_id
                staff_member.staff_id = new_staff_id
        
        # Ensure StaffProfile exists for primary assignment (extended staff info)
        if is_primary:
            staff_profile_result = await self.db.execute(
                select(StaffProfile).where(
                    and_(
                        StaffProfile.user_id == staff_member.id,
                        StaffProfile.hospital_id == self.hospital_id,
                    )
                )
            )
            staff_profile = staff_profile_result.scalar_one_or_none()

            # Human-friendly designation based on role
            role_display_map = {
                UserRole.DOCTOR: "Doctor",
                UserRole.NURSE: "Nurse",
                UserRole.RECEPTIONIST: "Receptionist",
                UserRole.PHARMACIST: "Pharmacist",
                UserRole.LAB_TECH: "Lab Technician",
            }
            designation = role_display_map.get(staff_role, "Staff") if staff_role else "Staff"
            joining_date_str = effective_from.date().isoformat()

            if not staff_profile:
                staff_profile = StaffProfile(
                    id=uuid.uuid4(),
                    hospital_id=self.hospital_id,
                    user_id=staff_member.id,
                    department_id=department.id,
                    employee_id=staff_member.staff_id or staff_member.email,
                    designation=designation,
                    joining_date=joining_date_str,
                    qualification=None,
                    experience_years=0,
                    specialization=department.name,
                    emergency_contact_name=None,
                    emergency_contact_phone=None,
                    emergency_contact_relation=None,
                    is_full_time=True,
                    salary=None,
                    skills=[],
                    certifications=[],
                )
                self.db.add(staff_profile)
            else:
                # Update primary department / designation if profile already exists
                staff_profile.department_id = department.id
                if not staff_profile.employee_id:
                    staff_profile.employee_id = staff_member.staff_id or staff_member.email
                if not staff_profile.designation:
                    staff_profile.designation = designation
                if not staff_profile.joining_date:
                    staff_profile.joining_date = joining_date_str

        assignment = StaffDepartmentAssignment(
            id=uuid.uuid4(),
            hospital_id=self.hospital_id,
            staff_id=staff_member.id,
            department_id=department.id,
            is_primary=is_primary,
            effective_from=effective_from,
            notes=assignment_data.get('notes'),
            is_active=True
        )
        
        self.db.add(assignment)

        md = dict(staff_member.user_metadata or {})
        md["department_id"] = str(department.id)
        md["department_name"] = department.name
        staff_member.user_metadata = md

        # ------------------------------------------------------------------
        # AUTO-CREATE DOCTOR PROFILE WHEN DOCTOR IS ASSIGNED TO DEPARTMENT
        # ------------------------------------------------------------------
        from app.models.doctor import DoctorProfile

        doctor_profile_created = False
        if UserRole.DOCTOR in staff_roles:
            existing_profile_result = await self.db.execute(
                select(DoctorProfile).where(
                    and_(
                        DoctorProfile.user_id == staff_member.id,
                        DoctorProfile.hospital_id == self.hospital_id
                    )
                )
            )
            existing_profile = existing_profile_result.scalar_one_or_none()

            if not existing_profile:
                doctor_id = staff_member.staff_id or f"DOC{str(uuid.uuid4())[:8].upper()}"
                exp_years = int(md.get("doctor_experience_years", 0) or 0)
                try:
                    cfee = Decimal(str(md.get("consultation_fee", 0) or 0))
                except Exception:
                    cfee = Decimal("0")
                spec_from_md = (
                    (md.get("doctor_specialization") or md.get("specialization") or "").strip()
                    or department.name
                )

                minimal_profile = DoctorProfile(
                    id=uuid.uuid4(),
                    hospital_id=self.hospital_id,
                    user_id=staff_member.id,
                    department_id=department.id,
                    doctor_id=doctor_id,
                    medical_license_number=f"AUTO-{doctor_id}",
                    designation="Doctor",
                    specialization=spec_from_md,
                    sub_specialization=None,
                    experience_years=exp_years,
                    qualifications=[],
                    certifications=[],
                    medical_associations=[],
                    consultation_fee=cfee,
                    follow_up_fee=None,
                    consultation_type=md.get("consultation_type"),
                    availability_time=md.get("availability_time"),
                    is_available_for_emergency=False,
                    is_accepting_new_patients=True,
                    bio=None,
                    languages_spoken=["English"],
                )
                self.db.add(minimal_profile)
                doctor_profile_created = True

        nurse_profile_created = False
        receptionist_profile_created = False
        from app.models.nurse import NurseProfile
        from app.models.receptionist import ReceptionistProfile

        shift_type = _shift_type_from_timing(md.get("shift_timing"))

        if UserRole.NURSE in staff_roles:
            existing_nurse = await self.db.execute(
                select(NurseProfile.id).where(
                    and_(
                        NurseProfile.user_id == staff_member.id,
                        NurseProfile.hospital_id == self.hospital_id,
                    )
                )
            )
            if not existing_nurse.scalar_one_or_none():
                nid = staff_member.staff_id or f"NUR{str(uuid.uuid4())[:8].upper()}"
                nlic = f"AUTO-NL-{uuid.uuid4().hex[:12]}".upper()
                self.db.add(
                    NurseProfile(
                        id=uuid.uuid4(),
                        hospital_id=self.hospital_id,
                        user_id=staff_member.id,
                        department_id=department.id,
                        nurse_id=nid,
                        nursing_license_number=nlic,
                        designation="Staff Nurse",
                        specialization=department.name,
                        experience_years=0,
                        shift_type=shift_type,
                    )
                )
                nurse_profile_created = True

        if UserRole.RECEPTIONIST in staff_roles:
            existing_rc = await self.db.execute(
                select(ReceptionistProfile.id).where(
                    and_(
                        ReceptionistProfile.user_id == staff_member.id,
                        ReceptionistProfile.hospital_id == self.hospital_id,
                    )
                )
            )
            if not existing_rc.scalar_one_or_none():
                rid = staff_member.staff_id or f"RC{str(uuid.uuid4())[:8].upper()}"
                eid = f"EMP-{uuid.uuid4().hex[:12].upper()}"
                rw_a = (md.get("receptionist_work_area") or "OPD").strip()
                try:
                    rey_a = max(0, int(md.get("receptionist_experience_years", 0) or 0))
                except (TypeError, ValueError):
                    rey_a = 0
                rdes_a = (md.get("receptionist_designation") or "Front Desk Receptionist").strip()
                self.db.add(
                    ReceptionistProfile(
                        id=uuid.uuid4(),
                        hospital_id=self.hospital_id,
                        user_id=staff_member.id,
                        department_id=department.id,
                        receptionist_id=rid,
                        employee_id=eid,
                        designation=rdes_a,
                        work_area=rw_a,
                        experience_years=rey_a,
                        shift_type=shift_type,
                    )
                )
                receptionist_profile_created = True

        await self.db.commit()
        
        return {
            "staff_name": staff_name,
            "department_name": department_name,
            "doctor_profile_created": doctor_profile_created,
            "nurse_profile_created": nurse_profile_created,
            "receptionist_profile_created": receptionist_profile_created,
            "message": f"Staff member '{staff_name}' assigned to department '{department_name}' successfully"
        }
    
    async def unassign_staff_from_department(self, unassignment_data: Dict[str, Any]) -> Dict[str, Any]:
        """Unassign staff member from a department"""
        staff_name = unassignment_data['staff_name']
        department_name = unassignment_data['department_name']
        
        # Find staff member by name
        staff_member = await self._get_staff_by_name(staff_name)
        if not staff_member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "STAFF_NOT_FOUND", "message": f"Staff member '{staff_name}' not found in this hospital"}
            )
        
        # Find department by name
        department = await self._get_department_by_name(department_name)
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DEPARTMENT_NOT_FOUND", "message": f"Department '{department_name}' not found in this hospital"}
            )
        
        # Find active assignment
        from app.models.hospital import StaffDepartmentAssignment
        assignment_result = await self.db.execute(
            select(StaffDepartmentAssignment).where(
                and_(
                    StaffDepartmentAssignment.staff_id == staff_member.id,
                    StaffDepartmentAssignment.department_id == department.id,
                    StaffDepartmentAssignment.is_active == True
                )
            )
        )
        assignment = assignment_result.scalar_one_or_none()
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ASSIGNMENT_NOT_FOUND", "message": f"Staff member is not assigned to {department_name}"}
            )
        
        # Deactivate assignment
        from datetime import datetime
        from app.core.utils import parse_date_string
        
        effective_to = parse_date_string(unassignment_data.get('effective_to')) or datetime.utcnow()
        
        assignment.is_active = False
        assignment.effective_to = effective_to
        assignment.unassignment_reason = unassignment_data.get('reason')
        assignment.updated_at = datetime.utcnow()
        
        await self.db.commit()
        
        return {
            "staff_name": staff_name,
            "department_name": department_name,
            "message": f"Staff member '{staff_name}' unassigned from department '{department_name}' successfully"
        }
    
    async def get_department_staff(self, department_name: str) -> List[Dict[str, Any]]:
        """Get all staff members assigned to a department"""
        # Find department by name
        department = await self._get_department_by_name(department_name)
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "DEPARTMENT_NOT_FOUND", "message": f"Department '{department_name}' not found in this hospital"}
            )
        
        # Get all active assignments for this department
        from app.models.hospital import StaffDepartmentAssignment
        query = select(StaffDepartmentAssignment).options(
            selectinload(StaffDepartmentAssignment.staff).selectinload(User.roles)
        ).where(
            and_(
                StaffDepartmentAssignment.department_id == department.id,
                StaffDepartmentAssignment.is_active == True
            )
        ).order_by(StaffDepartmentAssignment.is_primary.desc(), StaffDepartmentAssignment.effective_from.asc())
        
        result = await self.db.execute(query)
        assignments = result.scalars().all()
        
        staff_list = []
        for assignment in assignments:
            staff = assignment.staff
            staff_roles = [role.name for role in staff.roles]
            primary_role = next((role for role in staff_roles if role in [UserRole.DOCTOR, UserRole.NURSE, UserRole.RECEPTIONIST, UserRole.PHARMACIST, UserRole.LAB_TECH]), None)
            
            # Generate staff name with appropriate title
            staff_name = f"{staff.first_name} {staff.last_name}"
            if primary_role == UserRole.DOCTOR:
                staff_name = f"Dr. {staff_name}"
            elif primary_role == UserRole.NURSE:
                staff_name = f"Nurse {staff_name}"
            
            staff_list.append({
                "id": str(staff.id),
                "staff_id": staff.staff_id,
                "name": staff_name,
                "email": staff.email,
                "phone": staff.phone,
                "roles": staff_roles,
                "is_primary": assignment.is_primary,
                "effective_from": assignment.effective_from.isoformat(),
                "notes": assignment.notes,
                "assignment_id": str(assignment.id)
            })
        
        return staff_list
    
    async def get_staff_departments(self, staff_name: str) -> List[Dict[str, Any]]:
        """Get all departments assigned to a staff member"""
        # Find staff member by name
        staff_member = await self._get_staff_by_name(staff_name)
        if not staff_member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "STAFF_NOT_FOUND", "message": f"Staff member '{staff_name}' not found in this hospital"}
            )
        
        # Get all active assignments for this staff member
        from app.models.hospital import StaffDepartmentAssignment
        query = select(StaffDepartmentAssignment).options(
            selectinload(StaffDepartmentAssignment.department)
        ).where(
            and_(
                StaffDepartmentAssignment.staff_id == staff_member.id,
                StaffDepartmentAssignment.is_active == True
            )
        ).order_by(StaffDepartmentAssignment.is_primary.desc(), StaffDepartmentAssignment.effective_from.asc())
        
        result = await self.db.execute(query)
        assignments = result.scalars().all()
        
        department_list = []
        for assignment in assignments:
            department = assignment.department
            
            department_list.append({
                "id": str(department.id),
                "name": department.name,
                "code": department.code,
                "description": department.description,
                "is_primary": assignment.is_primary,
                "effective_from": assignment.effective_from.isoformat(),
                "notes": assignment.notes,
                "assignment_id": str(assignment.id)
            })
        
        return department_list
    
    async def _get_staff_by_name(self, staff_name: str) -> Optional[User]:
        """Get staff member by name within this hospital"""
        # Clean the name and remove common titles
        cleaned_name = staff_name.strip()
        
        # Remove common titles
        titles = ["Dr.", "Dr", "Doctor", "Prof.", "Prof", "Professor", "Nurse", "Mr.", "Ms.", "Mrs."]
        for title in titles:
            if cleaned_name.startswith(title + " "):
                cleaned_name = cleaned_name[len(title):].strip()
            elif cleaned_name.startswith(title + "."):
                cleaned_name = cleaned_name[len(title) + 1:].strip()
        
        # Split the cleaned name
        name_parts = cleaned_name.split()
        if len(name_parts) < 2:
            return None
        
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:])  # Handle multiple last names
        
        # Try exact match first
        query = select(User).options(selectinload(User.roles)).where(
            and_(
                User.hospital_id == self.hospital_id,
                User.roles.any(
                    Role.name.in_(
                        [
                            UserRole.DOCTOR.value,
                            UserRole.NURSE.value,
                            UserRole.RECEPTIONIST.value,
                            UserRole.PHARMACIST.value,
                            UserRole.LAB_TECH.value,
                        ]
                    )
                ),
                User.first_name.ilike(first_name),
                User.last_name.ilike(last_name)
            )
        )
        result = await self.db.execute(query)
        staff = result.scalar_one_or_none()
        
        if staff:
            return staff
        
        # If exact match fails, try partial match
        query = select(User).options(selectinload(User.roles)).where(
            and_(
                User.hospital_id == self.hospital_id,
                User.roles.any(
                    Role.name.in_(
                        [
                            UserRole.DOCTOR.value,
                            UserRole.NURSE.value,
                            UserRole.RECEPTIONIST.value,
                            UserRole.PHARMACIST.value,
                            UserRole.LAB_TECH.value,
                        ]
                    )
                ),
                User.first_name.ilike(f"%{first_name}%"),
                User.last_name.ilike(f"%{last_name}%")
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def _get_first_department(self) -> Optional[Department]:
        """First department in this hospital (for nurse/receptionist when department_name omitted)."""
        query = (
            select(Department)
            .where(Department.hospital_id == self.hospital_id)
            .order_by(Department.name.asc())
            .limit(1)
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def _get_department_by_name(self, department_name: str) -> Optional[Department]:
        """Get department by name within this hospital"""
        query = select(Department).where(
            and_(
                Department.hospital_id == self.hospital_id,
                Department.name.ilike(f"%{department_name}%")
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def _get_ward_by_name(self, ward_name: str) -> Optional['Ward']:
        """Get ward by name within this hospital"""
        from app.models.hospital import Ward
        query = select(Ward).where(
            and_(
                Ward.hospital_id == self.hospital_id,
                Ward.name.ilike(f"%{ward_name}%")
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()