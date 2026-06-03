"""
Doctor Service
Handles doctor-specific business logic including appointments, schedules, and patient consultations.
"""
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, date, time, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc, update, delete
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.user import User, Role, user_roles
from app.models.patient import PatientProfile, Appointment, MedicalRecord, Admission
from app.models.hospital import Department, StaffDepartmentAssignment, StaffProfile
from app.models.doctor import DoctorProfile
from app.models.nurse import NurseProfile
from app.models.receptionist import ReceptionistProfile
from app.models.schedule import DoctorSchedule
from app.core.enums import UserRole, AppointmentStatus, UserStatus, DayOfWeek
from app.core.utils import generate_patient_ref, parse_date_string, validate_medicine_id
from app.database.session import get_tenant_session_factory
from app.database.tenant_context import resolve_tenant_database_name_for_hospital

DEFAULT_APPOINTMENT_SLOT_MINUTES = 30


class DoctorService:
    """Service for doctor operations"""
    
    def __init__(self, db: AsyncSession, platform_db: Optional[AsyncSession] = None):
        self.db = db
        self.platform_db = platform_db if platform_db is not None else db

    def _db_sessions(self) -> List[AsyncSession]:
        seen: set[int] = set()
        sessions: List[AsyncSession] = []
        for sess in (self.db, self.platform_db):
            key = id(sess)
            if key in seen:
                continue
            seen.add(key)
            sessions.append(sess)
        return sessions

    async def _schedule_doctor_ids(
        self, user_id: uuid.UUID, hospital_id: uuid.UUID
    ) -> List[uuid.UUID]:
        """Schedules may reference users.id or legacy doctor_profiles.id."""
        ids: List[uuid.UUID] = [user_id]
        for session in self._db_sessions():
            profile_result = await session.execute(
                select(DoctorProfile.id).where(
                    and_(
                        DoctorProfile.user_id == user_id,
                        DoctorProfile.hospital_id == hospital_id,
                    )
                )
            )
            profile_id = profile_result.scalar_one_or_none()
            if profile_id and profile_id not in ids:
                ids.append(profile_id)
        return ids

    async def _fetch_active_schedules(
        self, user_id: uuid.UUID, hospital_id: uuid.UUID
    ) -> List[DoctorSchedule]:
        """Load schedule rows from tenant + platform DB (deduped)."""
        seen: set[uuid.UUID] = set()
        schedules: List[DoctorSchedule] = []
        scope_ids = await self._schedule_doctor_ids(user_id, hospital_id)
        for session in self._db_sessions():
            result = await session.execute(
                select(DoctorSchedule)
                .where(
                    and_(
                        DoctorSchedule.hospital_id == hospital_id,
                        DoctorSchedule.doctor_id.in_(scope_ids),
                        DoctorSchedule.is_active == True,
                    )
                )
                .order_by(DoctorSchedule.day_of_week, DoctorSchedule.start_time)
            )
            for schedule in result.scalars().all():
                if schedule.id in seen:
                    continue
                seen.add(schedule.id)
                schedules.append(schedule)
        return schedules

    async def _get_schedule_for_doctor(
        self,
        schedule_id: uuid.UUID,
        user_id: uuid.UUID,
        hospital_id: uuid.UUID,
    ) -> Optional[DoctorSchedule]:
        scope_ids = await self._schedule_doctor_ids(user_id, hospital_id)
        for session in self._db_sessions():
            result = await session.execute(
                select(DoctorSchedule).where(
                    and_(
                        DoctorSchedule.id == schedule_id,
                        DoctorSchedule.hospital_id == hospital_id,
                        DoctorSchedule.doctor_id.in_(scope_ids),
                    )
                )
            )
            schedule = result.scalar_one_or_none()
            if schedule:
                return schedule
        return None

    class _DepartmentRef:
        def __init__(self, department_id: uuid.UUID, name: str):
            self.id = department_id
            self.name = name

    class _DoctorRef:
        def __init__(self, user: User, department, specialization: str = "General"):
            self.user = user
            self.department = department
            self.id = user.id
            self.user_id = user.id
            self.hospital_id = user.hospital_id
            self.doctor_id = user.staff_id or f"DOC-{str(user.id)[:8]}"
            self.designation = "Doctor"
            self.specialization = specialization
            self.sub_specialization = None
            self.experience_years = 0
            self.consultation_fee = 500.0
            self.medical_license_number = f"LIC-{user.id}"
            self.is_available = True

    async def _department_from_user_metadata(self, user: User, db: Optional[AsyncSession] = None):
        """Fallback for platform-routed schedule APIs after hospital-admin assignment mirroring."""
        metadata = user.user_metadata if isinstance(user.user_metadata, dict) else {}
        raw_department_id = metadata.get("department_id")
        department_name = (
            metadata.get("department_name")
            or metadata.get("department")
            or "Assigned Department"
        )
        department_id = None
        if raw_department_id:
            try:
                department_id = uuid.UUID(str(raw_department_id))
            except (TypeError, ValueError):
                department_id = None

        department = None
        query_db = db or self.db
        if department_id:
            result = await query_db.execute(
                select(Department).where(
                    and_(
                        Department.id == department_id,
                        Department.hospital_id == user.hospital_id,
                    )
                )
            )
            department = result.scalar_one_or_none()
        if not department and department_name and user.hospital_id:
            result = await query_db.execute(
                select(Department).where(
                    and_(
                        Department.hospital_id == user.hospital_id,
                        func.lower(Department.name) == str(department_name).strip().lower(),
                    )
                )
            )
            department = result.scalar_one_or_none()

        if department:
            return department
        if department_id:
            return self._DepartmentRef(department_id, str(department_name))
        return None

    async def _department_from_tenant_staff_records(self, user_id: uuid.UUID, hospital_id: uuid.UUID):
        """Read tenant-side staff/profile assignment when platform mirror has not caught up."""
        tenant_name = await resolve_tenant_database_name_for_hospital(hospital_id)
        if not tenant_name:
            return None

        tenant_factory = get_tenant_session_factory(tenant_name)
        async with tenant_factory() as tenant_db:
            user = await tenant_db.get(User, user_id)
            if user:
                department = await self._department_from_user_metadata(user, tenant_db)
                if department:
                    return department

            assignment_result = await tenant_db.execute(
                select(StaffDepartmentAssignment)
                .where(
                    and_(
                        StaffDepartmentAssignment.staff_id == user_id,
                        StaffDepartmentAssignment.hospital_id == hospital_id,
                        StaffDepartmentAssignment.is_active == True,
                    )
                )
                .options(selectinload(StaffDepartmentAssignment.department))
                .order_by(
                    StaffDepartmentAssignment.is_primary.desc(),
                    StaffDepartmentAssignment.effective_from.desc(),
                )
                .limit(1)
            )
            assignment = assignment_result.scalars().first()
            if assignment:
                if assignment.department:
                    return self._DepartmentRef(
                        assignment.department.id,
                        assignment.department.name,
                    )
                return self._DepartmentRef(assignment.department_id, "Assigned Department")

            for model in (DoctorProfile, ReceptionistProfile, NurseProfile, StaffProfile):
                result = await tenant_db.execute(
                    select(model)
                    .where(
                        and_(
                            model.user_id == user_id,
                            model.hospital_id == hospital_id,
                        )
                    )
                    .options(selectinload(model.department))
                    .limit(1)
                )
                profile = result.scalars().first()
                if profile:
                    department = getattr(profile, "department", None)
                    department_id = getattr(profile, "department_id", None)
                    if department:
                        return self._DepartmentRef(department.id, department.name)
                    if department_id:
                        return self._DepartmentRef(department_id, "Assigned Department")
        return None
    
    @staticmethod
    def _appointment_patient_display_name(apt: Appointment) -> str:
        """Avoid 500s when patient/user rows are missing or not loaded."""
        patient = getattr(apt, "patient", None)
        if not patient:
            return "Patient"
        user = getattr(patient, "user", None)
        if not user:
            return "Patient"
        parts = [user.first_name or "", user.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or "Patient"

    @staticmethod
    def _schedule_date_parts(schedule_data: Dict[str, Any]) -> tuple[str, str]:
        """Return (date_iso, day_of_week) for the simplified schedule API."""
        raw_date = schedule_data.get("date") or schedule_data.get("effective_from")
        if raw_date:
            date_iso = datetime.strptime(str(raw_date), "%Y-%m-%d").date().isoformat()
            return date_iso, datetime.strptime(date_iso, "%Y-%m-%d").strftime("%A").upper()
        # Backward compatibility for old callers during rollout.
        day_of_week = str(schedule_data.get("day_of_week") or "").strip().upper()
        if not day_of_week:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date is required in YYYY-MM-DD format.",
            )
        return "", day_of_week

    @staticmethod
    def _schedule_date_from_row(schedule: DoctorSchedule) -> Optional[str]:
        if schedule.effective_from and schedule.effective_from == schedule.effective_to:
            return schedule.effective_from
        return schedule.effective_from

    @staticmethod
    def _schedule_date_value(value: Any) -> Optional[str]:
        """DoctorSchedule.effective_* are String(10) columns, not date objects."""
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip()[:10] if value.strip() else None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    
    # ============================================================================
    # USER CONTEXT AND VALIDATION
    # ============================================================================
    
    def get_user_context(self, current_user: User) -> dict:
        """Extract user context from JWT token"""
        user_roles = [role.name for role in current_user.roles]
        
        return {
            "user_id": str(current_user.id),
            "hospital_id": str(current_user.hospital_id),
            "role": user_roles[0] if user_roles else None,
            "all_roles": user_roles
        }
    
    async def validate_doctor_access(self, user_context: dict) -> None:
        """Ensure user is a doctor (any assigned role, not only the first)."""
        roles = user_context.get("all_roles") or []
        if UserRole.DOCTOR.value not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - Doctor role required"
            )
    
    async def get_doctor_profile(self, user_context: dict):
        """Get doctor profile with department information"""
        await self.validate_doctor_access(user_context)

        staff_uuid = uuid.UUID(str(user_context["user_id"]))
        
        # Get doctor user and their department assignment
        doctor_result = await self.db.execute(
            select(User)
            .where(User.id == staff_uuid)
        )
        doctor_user = doctor_result.scalar_one_or_none()
        
        if not doctor_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor user not found. Please contact administrator."
            )
            
        # Prefer primary active assignment; multiple rows must not use scalar_one_or_none()
        assignment_result = await self.db.execute(
            select(StaffDepartmentAssignment)
            .where(
                and_(
                    StaffDepartmentAssignment.staff_id == staff_uuid,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
            .options(selectinload(StaffDepartmentAssignment.department))
            .order_by(
                StaffDepartmentAssignment.is_primary.desc(),
                StaffDepartmentAssignment.effective_from.desc(),
            )
            .limit(1)
        )
        assignment = assignment_result.scalars().first()
        
        if not assignment:
            profile_result = await self.db.execute(
                select(DoctorProfile)
                .where(
                    and_(
                        DoctorProfile.user_id == staff_uuid,
                        DoctorProfile.hospital_id == doctor_user.hospital_id,
                    )
                )
                .options(
                    selectinload(DoctorProfile.user),
                    selectinload(DoctorProfile.department),
                )
            )
            doctor_profile = profile_result.scalar_one_or_none()
            if doctor_profile and doctor_profile.department:
                return doctor_profile
            if doctor_profile and doctor_profile.department_id:
                department = await self._department_from_user_metadata(doctor_user)
                if not department:
                    department = self._DepartmentRef(
                        doctor_profile.department_id,
                        "Assigned Department",
                    )
                if department:
                    return self._DoctorRef(
                        doctor_user,
                        department,
                        doctor_profile.specialization or "General",
                    )
            department = await self._department_from_user_metadata(doctor_user)
            if department:
                return self._DoctorRef(doctor_user, department)
            if doctor_user.hospital_id:
                department = await self._department_from_tenant_staff_records(
                    staff_uuid,
                    doctor_user.hospital_id,
                )
                if department:
                    return self._DoctorRef(doctor_user, department)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Doctor department assignment not found. Assign the doctor to a department "
                    "or set department_id on the doctor profile."
                ),
            )
            
        department = assignment.department or self._DepartmentRef(
            assignment.department_id,
            "Assigned Department",
        )
        return self._DoctorRef(
            doctor_user,
            department,
            department.name if department else "General",
        )
    
    async def get_or_create_doctor_profile(self, user_context: dict, doctor):
        """Get or create doctor profile for prescription operations"""
        from app.models.doctor import DoctorProfile
        
        doctor_profile_result = await self.db.execute(
            select(DoctorProfile).where(DoctorProfile.user_id == doctor.user_id)
        )
        doctor_profile = doctor_profile_result.scalar_one_or_none()
        
        if not doctor_profile:
            # Create a doctor profile if it doesn't exist
            doctor_profile = DoctorProfile(
                id=uuid.uuid4(),
                hospital_id=uuid.UUID(user_context["hospital_id"]),
                user_id=doctor.user_id,
                department_id=doctor.department.id,
                doctor_id=doctor.doctor_id,
                medical_license_number=doctor.medical_license_number,
                designation=doctor.designation,
                specialization=doctor.specialization,
                experience_years=doctor.experience_years,
                consultation_fee=doctor.consultation_fee,
                is_available_for_emergency=False,
                is_accepting_new_patients=True
            )
            self.db.add(doctor_profile)
            await self.db.flush()  # Get the ID without committing
        
        return doctor_profile
    
    # ============================================================================
    # SCHEDULE MANAGEMENT
    # ============================================================================
    
    async def get_weekly_schedule(self, week_start: Optional[str], current_user: User) -> Dict[str, Any]:
        """Get doctor's weekly schedule with appointments"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Calculate week dates
        if week_start:
            start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
        else:
            today = date.today()
            start_date = today - timedelta(days=today.weekday())
        
        end_date = start_date + timedelta(days=6)
        
        schedules = await self._fetch_active_schedules(doctor.user_id, doctor.hospital_id)
        scope_ids = await self._schedule_doctor_ids(doctor.user_id, doctor.hospital_id)
        
        # Get appointments for the week
        appointments_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.hospital_id == doctor.hospital_id,
                    Appointment.doctor_id.in_(scope_ids),
                    Appointment.appointment_date >= start_date.isoformat(),
                    Appointment.appointment_date <= end_date.isoformat()
                )
            )
            .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
            .order_by(asc(Appointment.appointment_date), asc(Appointment.appointment_time))
        )
        
        appointments = appointments_result.scalars().all()
        
        # Build daily schedules
        daily_schedules = []
        total_slots = 0
        total_appointments = len(appointments)
        
        for i in range(7):
            current_date = start_date + timedelta(days=i)
            day_name = current_date.strftime("%A").upper()
            
            # Find schedule for this date; exact date rows override older weekly rows.
            current_iso = current_date.isoformat()
            day_candidates = [
                s for s in schedules
                if (s.day_of_week or "").strip().upper() == day_name
                and (not s.effective_from or s.effective_from <= current_iso)
                and (not s.effective_to or s.effective_to >= current_iso)
            ]
            day_candidates.sort(
                key=lambda s: (
                    s.effective_from == current_iso and s.effective_to == current_iso,
                    s.created_at,
                ),
                reverse=True,
            )
            day_schedule = day_candidates[0] if day_candidates else None
            
            # Get appointments for this day
            day_appointments = [a for a in appointments if a.appointment_date == current_date.isoformat()]
            
            if day_schedule and day_schedule.slot_duration_minutes and day_schedule.slot_duration_minutes >= 15:
                # Calculate available slots (slot length must be set on the schedule row)
                start_time = datetime.strptime(day_schedule.start_time.strftime("%H:%M"), "%H:%M")
                end_time = datetime.strptime(day_schedule.end_time.strftime("%H:%M"), "%H:%M")
                slot_duration = timedelta(minutes=day_schedule.slot_duration_minutes)
                
                # Calculate break time
                break_duration = timedelta(0)
                if day_schedule.break_start_time and day_schedule.break_end_time:
                    break_start = datetime.strptime(day_schedule.break_start_time.strftime("%H:%M"), "%H:%M")
                    break_end = datetime.strptime(day_schedule.break_end_time.strftime("%H:%M"), "%H:%M")
                    break_duration = break_end - break_start
                
                # Calculate total available time
                total_time = end_time - start_time - break_duration
                day_total_slots = max(
                    0,
                    int(total_time.total_seconds() / slot_duration.total_seconds()),
                )
                total_slots += day_total_slots
                
                daily_schedules.append({
                    "date": current_date.isoformat(),
                    "day_name": day_name,
                    "has_schedule": True,
                    "start_time": day_schedule.start_time.strftime("%H:%M"),
                    "end_time": day_schedule.end_time.strftime("%H:%M"),
                    "slot_duration_minutes": day_schedule.slot_duration_minutes,
                    "break_start_time": day_schedule.break_start_time.strftime("%H:%M") if day_schedule.break_start_time else None,
                    "break_end_time": day_schedule.break_end_time.strftime("%H:%M") if day_schedule.break_end_time else None,
                    "total_slots": day_total_slots,
                    "booked_appointments": len(day_appointments),
                    "available_slots": day_total_slots - len(day_appointments),
                    "appointments": [
                        {
                            "appointment_ref": apt.appointment_ref,
                            "patient_name": self._appointment_patient_display_name(apt),
                            "appointment_time": apt.appointment_time,
                            "status": apt.status,
                            "chief_complaint": apt.chief_complaint
                        } for apt in day_appointments
                    ]
                })
            elif day_schedule:
                daily_schedules.append({
                    "date": current_date.isoformat(),
                    "day_name": day_name,
                    "has_schedule": True,
                    "start_time": day_schedule.start_time.strftime("%H:%M"),
                    "end_time": day_schedule.end_time.strftime("%H:%M"),
                    "slot_duration_minutes": day_schedule.slot_duration_minutes,
                    "total_slots": 0,
                    "booked_appointments": len(day_appointments),
                    "available_slots": 0,
                    "note": "Set slot_duration_minutes (15–120) on this schedule to enable bookable slots.",
                    "appointments": [
                        {
                            "appointment_ref": apt.appointment_ref,
                            "patient_name": self._appointment_patient_display_name(apt),
                            "appointment_time": apt.appointment_time,
                            "status": apt.status,
                            "chief_complaint": apt.chief_complaint
                        } for apt in day_appointments
                    ],
                })
            else:
                daily_schedules.append({
                    "date": current_date.isoformat(),
                    "day_name": day_name,
                    "has_schedule": False,
                    "total_slots": 0,
                    "booked_appointments": len(day_appointments),
                    "available_slots": 0,
                    "appointments": []
                })
        
        available_slots = total_slots - total_appointments
        
        doc_first = (getattr(doctor.user, "first_name", None) or "").strip()
        doc_last = (getattr(doctor.user, "last_name", None) or "").strip()
        doc_display = " ".join(p for p in (doc_first, doc_last) if p).strip()
        return {
            "week_start": start_date.isoformat(),
            "week_end": end_date.isoformat(),
            "doctor_name": f"Dr. {doc_display}" if doc_display else "Dr.",
            "total_slots": total_slots,
            "total_appointments": total_appointments,
            "available_slots": available_slots,
            "daily_schedules": daily_schedules
        }
    
    async def get_schedule_slots(self, current_user: User) -> Dict[str, Any]:
        """Get doctor's schedule slots configuration"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        schedules = await self._fetch_active_schedules(doctor.user_id, doctor.hospital_id)
        
        # Format schedules
        schedule_slots = []
        for schedule in schedules:
            schedule_slots.append({
                "schedule_id": str(schedule.id),
                "date": self._schedule_date_from_row(schedule),
                "start_time": schedule.start_time.strftime("%H:%M"),
                "end_time": schedule.end_time.strftime("%H:%M"),
            })
        
        doc_first = (getattr(doctor.user, "first_name", None) or "").strip()
        doc_last = (getattr(doctor.user, "last_name", None) or "").strip()
        doc_display = " ".join(p for p in (doc_first, doc_last) if p).strip()
        return {
            "doctor_name": f"Dr. {doc_display}" if doc_display else "Dr.",
            "department": doctor.department.name,
            "total_schedules": len(schedule_slots),
            "schedules": schedule_slots
        }

    async def get_schedule_slot_by_id(self, schedule_id: str, current_user: User) -> Dict[str, Any]:
        """Get one schedule slot by id for the current doctor."""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        try:
            sid = uuid.UUID(schedule_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid schedule_id",
            )
        schedule = await self._get_schedule_for_doctor(
            sid, doctor.user_id, doctor.hospital_id
        )
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found",
            )
        return {
            "schedule_id": str(schedule.id),
            "date": self._schedule_date_from_row(schedule),
            "day_of_week": schedule.day_of_week,
            "start_time": schedule.start_time.strftime("%H:%M"),
            "end_time": schedule.end_time.strftime("%H:%M"),
            "slot_duration_minutes": schedule.slot_duration_minutes,
            "is_active": schedule.is_active,
            "effective_from": self._schedule_date_value(schedule.effective_from),
            "effective_to": self._schedule_date_value(schedule.effective_to),
            "notes": schedule.notes,
        }
    
    async def create_schedule_slot(self, schedule_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Create a new schedule slot for the doctor"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        schedule_date, day_of_week = self._schedule_date_parts(schedule_data)

        # Check if schedule already exists for this date.
        conflict_filters = [
            DoctorSchedule.doctor_id == doctor.user_id,
            DoctorSchedule.day_of_week == day_of_week,
            DoctorSchedule.is_active == True,
        ]
        if schedule_date:
            conflict_filters.extend(
                [
                    DoctorSchedule.effective_from == schedule_date,
                    DoctorSchedule.effective_to == schedule_date,
                ]
            )
        existing_schedule = await self.db.execute(
            select(DoctorSchedule)
            .where(and_(*conflict_filters))
        )
        
        if existing_schedule.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Schedule already exists for {schedule_date or day_of_week}"
            )
        
        # Parse time strings
        start_time = datetime.strptime(schedule_data["start_time"], "%H:%M").time()
        end_time = datetime.strptime(schedule_data["end_time"], "%H:%M").time()
        
        if start_time >= end_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_time must be before end_time.",
            )

        slot_mins = int(schedule_data.get("slot_duration_minutes") or DEFAULT_APPOINTMENT_SLOT_MINUTES)
        if slot_mins < 15 or slot_mins > 120:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slot_duration_minutes must be between 15 and 120.",
            )

        # Create schedule
        schedule = DoctorSchedule(
            id=uuid.uuid4(),
            hospital_id=uuid.UUID(str(user_context["hospital_id"])),
            doctor_id=doctor.user_id,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            slot_duration_minutes=slot_mins,
            max_patients_per_slot=1,
            break_start_time=None,
            break_end_time=None,
            effective_from=schedule_date or None,
            effective_to=schedule_date or None,
            notes=None,
            is_emergency_available=False
        )
        
        self.db.add(schedule)
        await self.db.commit()
        
        return {
            "schedule_id": str(schedule.id),
            "date": self._schedule_date_from_row(schedule),
            "start_time": schedule.start_time.strftime("%H:%M"),
            "end_time": schedule.end_time.strftime("%H:%M"),
            "message": "Schedule slot created successfully"
        }
    
    async def update_schedule_slot(self, schedule_id: str, update_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Update an existing schedule slot"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get schedule
        schedule_result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.id == uuid.UUID(schedule_id),
                    DoctorSchedule.doctor_id == doctor.user_id
                )
            )
        )
        
        schedule = schedule_result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found"
            )
        
        if "date" in update_data and update_data["date"] is not None:
            date_iso, day_of_week = self._schedule_date_parts(update_data)
            schedule.day_of_week = day_of_week
            schedule.effective_from = date_iso
            schedule.effective_to = date_iso

        if update_data.get("start_time"):
            schedule.start_time = datetime.strptime(update_data["start_time"], "%H:%M").time()
        if update_data.get("end_time"):
            schedule.end_time = datetime.strptime(update_data["end_time"], "%H:%M").time()

        if schedule.start_time >= schedule.end_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_time must be before end_time.",
            )
        
        await self.db.commit()
        
        return {
            "schedule_id": str(schedule.id),
            "message": "Schedule slot updated successfully"
        }
    
    async def delete_schedule_slot(self, schedule_id: str, current_user: User) -> Dict[str, Any]:
        """Delete a schedule slot"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get schedule
        schedule_result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.id == uuid.UUID(schedule_id),
                    DoctorSchedule.doctor_id == doctor.user_id
                )
            )
        )
        
        schedule = schedule_result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found"
            )
        
        await self.db.delete(schedule)
        await self.db.commit()
        
        return {
            "schedule_id": str(schedule.id),
            "message": "Schedule slot deleted successfully"
        }
    
    # ============================================================================
    # STAFF-MANAGED DOCTOR SCHEDULES (receptionist / nurse)
    # ============================================================================
    
    async def _get_staff_department_id_for_schedule(self, acting_user: User) -> uuid.UUID:
        if not acting_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital context required to manage doctor schedules.",
            )
        r = await self.db.execute(
            select(StaffDepartmentAssignment.department_id)
            .where(
                and_(
                    StaffDepartmentAssignment.staff_id == acting_user.id,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
            .order_by(
                StaffDepartmentAssignment.is_primary.desc(),
                StaffDepartmentAssignment.effective_from.desc(),
            )
            .limit(1)
        )
        dept_id = r.scalar_one_or_none()
        if not dept_id:
            receptionist_result = await self.db.execute(
                select(ReceptionistProfile.department_id).where(
                    and_(
                        ReceptionistProfile.user_id == acting_user.id,
                        ReceptionistProfile.hospital_id == acting_user.hospital_id,
                        ReceptionistProfile.is_active == True,
                    )
                )
            )
            dept_id = receptionist_result.scalar_one_or_none()
        if not dept_id:
            nurse_result = await self.db.execute(
                select(NurseProfile.department_id).where(
                    and_(
                        NurseProfile.user_id == acting_user.id,
                        NurseProfile.hospital_id == acting_user.hospital_id,
                        NurseProfile.is_active == True,
                    )
                )
            )
            dept_id = nurse_result.scalar_one_or_none()
        if not dept_id:
            department = await self._department_from_user_metadata(acting_user)
            dept_id = department.id if department else None
        if not dept_id:
            department = await self._department_from_tenant_staff_records(
                acting_user.id,
                acting_user.hospital_id,
            )
            dept_id = department.id if department else None
        if not dept_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No active department found. Ask your admin to assign you to a department "
                    "or set department_id on your staff profile."
                ),
            )
        return dept_id
    
    async def get_staff_department_name(self, acting_user: User) -> Optional[str]:
        """Display name of the department the acting nurse/receptionist is assigned to."""
        dept_id = await self._get_staff_department_id_for_schedule(acting_user)
        row = await self.db.execute(select(Department.name).where(Department.id == dept_id))
        return row.scalar_one_or_none()
    
    async def get_target_doctor_in_hospital_for_staff_by_name(
        self, acting_user: User, doctor_name: str
    ) -> User:
        """
        Resolve doctor by display name; must be in the same hospital and same department
        as the acting receptionist/nurse (via StaffDepartmentAssignment).
        """
        raw = (doctor_name or "").strip()
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="doctor_name is required (e.g. Dr. Jane Smith or Jane Smith).",
            )
        dept_id = await self._get_staff_department_id_for_schedule(acting_user)
        q = (
            select(User)
            .join(user_roles, User.id == user_roles.c.user_id)
            .join(Role, user_roles.c.role_id == Role.id)
            .join(
                StaffDepartmentAssignment,
                and_(
                    StaffDepartmentAssignment.staff_id == User.id,
                    StaffDepartmentAssignment.department_id == dept_id,
                    StaffDepartmentAssignment.is_active == True,
                ),
            )
            .where(
                and_(
                    User.hospital_id == acting_user.hospital_id,
                    User.status == UserStatus.ACTIVE,
                    Role.name == UserRole.DOCTOR.value,
                    or_(
                        func.concat("Dr. ", User.first_name, " ", User.last_name).ilike(
                            f"%{raw}%"
                        ),
                        func.concat(User.first_name, " ", User.last_name).ilike(f"%{raw}%"),
                    ),
                )
            )
            .options(selectinload(User.roles))
        )
        result = await self.db.execute(q)
        doctors = list(result.scalars().unique().all())
        if not doctors:
            profile_query = (
                select(User)
                .join(user_roles, User.id == user_roles.c.user_id)
                .join(Role, user_roles.c.role_id == Role.id)
                .join(DoctorProfile, DoctorProfile.user_id == User.id)
                .where(
                    and_(
                        User.hospital_id == acting_user.hospital_id,
                        User.status == UserStatus.ACTIVE,
                        Role.name == UserRole.DOCTOR.value,
                        DoctorProfile.department_id == dept_id,
                        DoctorProfile.hospital_id == acting_user.hospital_id,
                        or_(
                            func.concat("Dr. ", User.first_name, " ", User.last_name).ilike(
                                f"%{raw}%"
                            ),
                            func.concat(User.first_name, " ", User.last_name).ilike(f"%{raw}%"),
                        ),
                    )
                )
                .options(selectinload(User.roles))
            )
            profile_result = await self.db.execute(profile_query)
            doctors = list(profile_result.scalars().unique().all())
        if not doctors:
            user_query = (
                select(User)
                .join(user_roles, User.id == user_roles.c.user_id)
                .join(Role, user_roles.c.role_id == Role.id)
                .where(
                    and_(
                        User.hospital_id == acting_user.hospital_id,
                        User.status == UserStatus.ACTIVE,
                        Role.name == UserRole.DOCTOR.value,
                        or_(
                            func.concat("Dr. ", User.first_name, " ", User.last_name).ilike(
                                f"%{raw}%"
                            ),
                            func.concat(User.first_name, " ", User.last_name).ilike(f"%{raw}%"),
                        ),
                    )
                )
                .options(selectinload(User.roles))
            )
            user_result = await self.db.execute(user_query)
            candidates = list(user_result.scalars().unique().all())
            doctors = []
            for candidate in candidates:
                department = await self._department_from_user_metadata(candidate)
                if not department and candidate.hospital_id:
                    department = await self._department_from_tenant_staff_records(
                        candidate.id,
                        candidate.hospital_id,
                    )
                if department and department.id == dept_id:
                    doctors.append(candidate)
        if len(doctors) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Multiple doctors in your department match that name. "
                    "Use a fuller name (e.g. include last name)."
                ),
            )
        if not doctors:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No doctor matching '{raw}' in your department. "
                    "They must be assigned to the same department as you."
                ),
            )
        return doctors[0]
    
    async def _ensure_schedule_doctor_in_staff_department(
        self, acting_user: User, doctor_user_id: uuid.UUID
    ) -> None:
        dept_id = await self._get_staff_department_id_for_schedule(acting_user)
        r = await self.db.execute(
            select(StaffDepartmentAssignment.id).where(
                and_(
                    StaffDepartmentAssignment.staff_id == doctor_user_id,
                    StaffDepartmentAssignment.department_id == dept_id,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
        )
        if r.scalar_one_or_none():
            return
        profile_result = await self.db.execute(
            select(DoctorProfile.id).where(
                and_(
                    DoctorProfile.user_id == doctor_user_id,
                    DoctorProfile.department_id == dept_id,
                    DoctorProfile.hospital_id == acting_user.hospital_id,
                )
            )
        )
        if not profile_result.scalar_one_or_none():
            user_result = await self.db.execute(
                select(User).where(
                    and_(
                        User.id == doctor_user_id,
                        User.hospital_id == acting_user.hospital_id,
                    )
                )
            )
            doctor_user = user_result.scalar_one_or_none()
            if doctor_user:
                department = await self._department_from_user_metadata(doctor_user)
                if not department and doctor_user.hospital_id:
                    department = await self._department_from_tenant_staff_records(
                        doctor_user.id,
                        doctor_user.hospital_id,
                    )
                if department and department.id == dept_id:
                    return
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="That schedule belongs to a doctor outside your department.",
            )
    
    async def get_schedule_slots_for_target_doctor(
        self, acting_user: User, doctor_name: str
    ) -> Dict[str, Any]:
        """Weekly schedule template rows for a doctor (same shape as doctor self-service)."""
        doctor_user = await self.get_target_doctor_in_hospital_for_staff_by_name(
            acting_user, doctor_name
        )
        schedules_result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.doctor_id == doctor_user.id,
                    DoctorSchedule.hospital_id == acting_user.hospital_id,
                    DoctorSchedule.is_active == True,
                )
            )
            .order_by(DoctorSchedule.day_of_week)
        )
        schedules = schedules_result.scalars().all()
        schedule_slots = []
        for schedule in schedules:
            schedule_slots.append(
                {
                    "schedule_id": str(schedule.id),
                    "date": self._schedule_date_from_row(schedule),
                    "start_time": schedule.start_time.strftime("%H:%M"),
                    "end_time": schedule.end_time.strftime("%H:%M"),
                }
            )
        dept_nm = await self.get_staff_department_name(acting_user)
        return {
            "doctor_user_id": str(doctor_user.id),
            "doctor_name": f"Dr. {doctor_user.first_name} {doctor_user.last_name}",
            "department_name": dept_nm,
            "total_schedules": len(schedule_slots),
            "schedules": schedule_slots,
        }
    
    async def create_schedule_slot_for_staff(
        self,
        acting_user: User,
        doctor_name: str,
        schedule_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        doctor_user = await self.get_target_doctor_in_hospital_for_staff_by_name(
            acting_user, doctor_name
        )
        schedule_date, day_of_week = self._schedule_date_parts(schedule_data)
        conflict_filters = [
            DoctorSchedule.doctor_id == doctor_user.id,
            DoctorSchedule.day_of_week == day_of_week,
            DoctorSchedule.hospital_id == acting_user.hospital_id,
            DoctorSchedule.is_active == True,
        ]
        if schedule_date:
            conflict_filters.extend(
                [
                    DoctorSchedule.effective_from == schedule_date,
                    DoctorSchedule.effective_to == schedule_date,
                ]
            )
        existing_schedule = await self.db.execute(
            select(DoctorSchedule)
            .where(and_(*conflict_filters))
        )
        if existing_schedule.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Schedule already exists for {schedule_date or day_of_week}",
            )
        start_time = datetime.strptime(schedule_data["start_time"], "%H:%M").time()
        end_time = datetime.strptime(schedule_data["end_time"], "%H:%M").time()
        if start_time >= end_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_time must be before end_time.",
            )
        slot_mins = int(schedule_data.get("slot_duration_minutes") or DEFAULT_APPOINTMENT_SLOT_MINUTES)
        if slot_mins < 15 or slot_mins > 120:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slot_duration_minutes must be between 15 and 120.",
            )

        schedule = DoctorSchedule(
            id=uuid.uuid4(),
            hospital_id=acting_user.hospital_id,
            doctor_id=doctor_user.id,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            slot_duration_minutes=slot_mins,
            max_patients_per_slot=1,
            break_start_time=None,
            break_end_time=None,
            effective_from=schedule_date or None,
            effective_to=schedule_date or None,
            notes=None,
            is_emergency_available=False,
        )
        self.db.add(schedule)
        await self.db.commit()
        return {
            "schedule_id": str(schedule.id),
            "doctor_user_id": str(doctor_user.id),
            "date": self._schedule_date_from_row(schedule),
            "start_time": schedule.start_time.strftime("%H:%M"),
            "end_time": schedule.end_time.strftime("%H:%M"),
            "message": "Schedule slot created successfully",
        }
    
    async def update_schedule_slot_for_staff(
        self,
        acting_user: User,
        schedule_id: str,
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not acting_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital context required",
            )
        schedule_result = await self.db.execute(
            select(DoctorSchedule).where(
                and_(
                    DoctorSchedule.id == uuid.UUID(schedule_id),
                    DoctorSchedule.hospital_id == acting_user.hospital_id,
                )
            )
        )
        schedule = schedule_result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found",
            )
        await self._ensure_schedule_doctor_in_staff_department(acting_user, schedule.doctor_id)
        if "date" in update_data and update_data["date"] is not None:
            date_iso, day_of_week = self._schedule_date_parts(update_data)
            schedule.day_of_week = day_of_week
            schedule.effective_from = date_iso
            schedule.effective_to = date_iso
        if update_data.get("start_time"):
            schedule.start_time = datetime.strptime(update_data["start_time"], "%H:%M").time()
        if update_data.get("end_time"):
            schedule.end_time = datetime.strptime(update_data["end_time"], "%H:%M").time()
        if schedule.start_time >= schedule.end_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_time must be before end_time.",
            )
        await self.db.commit()
        return {
            "schedule_id": str(schedule.id),
            "doctor_user_id": str(schedule.doctor_id),
            "message": "Schedule slot updated successfully",
        }
    
    async def delete_schedule_slot_for_staff(
        self, acting_user: User, schedule_id: str
    ) -> Dict[str, Any]:
        if not acting_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital context required",
            )
        schedule_result = await self.db.execute(
            select(DoctorSchedule).where(
                and_(
                    DoctorSchedule.id == uuid.UUID(schedule_id),
                    DoctorSchedule.hospital_id == acting_user.hospital_id,
                )
            )
        )
        schedule = schedule_result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found",
            )
        await self._ensure_schedule_doctor_in_staff_department(acting_user, schedule.doctor_id)
        doc_id = schedule.doctor_id
        await self.db.delete(schedule)
        await self.db.commit()
        return {
            "schedule_id": schedule_id,
            "doctor_user_id": str(doc_id),
            "message": "Schedule slot deleted successfully",
        }
    
    # ============================================================================
    # APPOINTMENT MANAGEMENT
    # ============================================================================
    
    async def get_doctor_appointments(self, filters: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Get doctor's appointments with filtering options"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Build query conditions
        # conditions = [Appointment.doctor_id == doctor.id]
        conditions = [Appointment.doctor_id == doctor.user_id]
        
        
        if filters.get("date_from"):
            conditions.append(Appointment.appointment_date >= filters["date_from"])
        
        if filters.get("date_to"):
            conditions.append(Appointment.appointment_date <= filters["date_to"])
        
        if filters.get("status"):
            conditions.append(Appointment.status == filters["status"])
        
        # Get appointments
        appointments_result = await self.db.execute(
            select(Appointment)
            .where(and_(*conditions))
            .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
            .order_by(desc(Appointment.appointment_date), desc(Appointment.appointment_time))
            .limit(filters.get("limit", 50))
        )
        
        appointments = appointments_result.scalars().all()
        
        # Format appointments
        appointment_list = []
        for appointment in appointments:
            patient_age = self.calculate_age(appointment.patient.date_of_birth)
            
            appointment_list.append({
                "appointment_ref": appointment.appointment_ref,
                "patient_ref": appointment.patient.patient_id,
                "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
                "patient_age": patient_age,
                "patient_phone": appointment.patient.user.phone,
                "appointment_date": appointment.appointment_date,
                "appointment_time": appointment.appointment_time,
                "duration_minutes": appointment.duration_minutes,
                "status": appointment.status,
                "appointment_type": appointment.appointment_type,
                "chief_complaint": appointment.chief_complaint,
                "notes": appointment.notes,
                "is_checked_in": appointment.checked_in_at is not None,
                "checked_in_at": appointment.checked_in_at.isoformat() if appointment.checked_in_at else None,
                "is_completed": appointment.status == AppointmentStatus.COMPLETED,
                "completed_at": appointment.completed_at.isoformat() if appointment.completed_at else None,
                "consultation_fee": float(appointment.consultation_fee) if appointment.consultation_fee else None,
                "is_paid": appointment.is_paid
            })
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "total_appointments": len(appointment_list),
            "filters": {
                "date_from": filters.get("date_from"),
                "date_to": filters.get("date_to"),
                "status": filters.get("status")
            },
            "appointments": appointment_list
        }
    
    async def get_appointment_details(self, appointment_ref: str, current_user: User) -> Dict[str, Any]:
        """Get detailed appointment information"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.doctor_id == doctor.id
                )
            )
            .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        patient_age = self.calculate_age(appointment.patient.date_of_birth)
        
        return {
            "appointment_ref": appointment.appointment_ref,
            "patient_ref": appointment.patient.patient_id,
            "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
            "patient_age": patient_age,
            "patient_phone": appointment.patient.user.phone,
            "appointment_date": appointment.appointment_date,
            "appointment_time": appointment.appointment_time,
            "duration_minutes": appointment.duration_minutes,
            "status": appointment.status,
            "appointment_type": appointment.appointment_type,
            "chief_complaint": appointment.chief_complaint,
            "notes": appointment.notes,
            "is_checked_in": appointment.checked_in_at is not None,
            "checked_in_at": appointment.checked_in_at.isoformat() if appointment.checked_in_at else None,
            "is_completed": appointment.status == AppointmentStatus.COMPLETED,
            "completed_at": appointment.completed_at.isoformat() if appointment.completed_at else None,
            "consultation_fee": float(appointment.consultation_fee) if appointment.consultation_fee else None,
            "is_paid": appointment.is_paid
        }
    
    async def update_appointment(self, appointment_ref: str, update_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Update appointment details"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.doctor_id == doctor.id
                )
            )
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        # Check if appointment can be updated
        if appointment.status == AppointmentStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update completed appointment"
            )
        
        if appointment.status == AppointmentStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update cancelled appointment"
            )
        
        # Build update data
        update_fields = {}
        
        if update_data.get("appointment_date"):
            # Validate date is not in the past
            appointment_date = datetime.strptime(update_data["appointment_date"], "%Y-%m-%d").date()
            if appointment_date < date.today():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot schedule appointment in the past"
                )
            update_fields["appointment_date"] = update_data["appointment_date"]
        
        if update_data.get("appointment_time"):
            update_fields["appointment_time"] = update_data["appointment_time"]
        
        if update_data.get("duration_minutes"):
            update_fields["duration_minutes"] = update_data["duration_minutes"]
        
        if update_data.get("appointment_type"):
            update_fields["appointment_type"] = update_data["appointment_type"]
        
        if "notes" in update_data:
            update_fields["notes"] = update_data["notes"]
        
        if "consultation_fee" in update_data:
            update_fields["consultation_fee"] = update_data["consultation_fee"]
        
        # Apply updates
        if update_fields:
            await self.db.execute(
                update(Appointment)
                .where(Appointment.id == appointment.id)
                .values(**update_fields)
            )
            await self.db.commit()
        
        return {
            "message": "Appointment updated successfully",
            "appointment_ref": appointment_ref,
            "updated_fields": list(update_fields.keys())
        }
    
    async def complete_appointment(self, appointment_ref: str, current_user: User) -> Dict[str, Any]:
        """Mark appointment as completed"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.doctor_id == doctor.id
                )
            )
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        # Check if appointment can be completed
        if appointment.status == AppointmentStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Appointment is already completed"
            )
        
        if appointment.status == AppointmentStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot complete cancelled appointment"
            )
        
        # Update appointment status
        await self.db.execute(
            update(Appointment)
            .where(Appointment.id == appointment.id)
            .values(
                status=AppointmentStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc)
            )
        )
        await self.db.commit()
        
        return {
            "message": "Appointment completed successfully",
            "appointment_ref": appointment_ref,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def cancel_appointment(self, appointment_ref: str, cancellation_reason: str, current_user: User) -> Dict[str, Any]:
        """Cancel appointment"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.doctor_id == doctor.id
                )
            )
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        # Check if appointment can be cancelled
        if appointment.status == AppointmentStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel completed appointment"
            )
        
        if appointment.status == AppointmentStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Appointment is already cancelled"
            )
        
        # Update appointment status
        await self.db.execute(
            update(Appointment)
            .where(Appointment.id == appointment.id)
            .values(
                status=AppointmentStatus.CANCELLED,
                cancelled_at=datetime.now(timezone.utc),
                cancellation_reason=cancellation_reason
            )
        )
        await self.db.commit()
        
        return {
            "message": "Appointment cancelled successfully",
            "appointment_ref": appointment_ref,
            "cancellation_reason": cancellation_reason,
            "cancelled_at": datetime.now(timezone.utc).isoformat()
        }
    
    # ============================================================================
    # PATIENT CONSULTATION
    # ============================================================================
    
    async def get_patient_consultation_details(self, patient_ref: str, current_user: User) -> Dict[str, Any]:
        """Get comprehensive patient details for consultation"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get patient
        patient_result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == user_context["hospital_id"]
                )
            )
            .options(selectinload(PatientProfile.user))
        )
        
        patient = patient_result.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient not found"
            )
        
        # Get current appointment (if any)
        current_appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.patient_id == patient.id,
                    Appointment.doctor_id == doctor.id,
                    Appointment.appointment_date == date.today().isoformat(),
                    Appointment.status.in_([AppointmentStatus.CONFIRMED])
                )
            )
            .order_by(desc(Appointment.appointment_time))
            .limit(1)
        )
        
        current_appointment = current_appointment_result.scalar_one_or_none()
        
        # Get medical history (last 10 records)
        medical_history_result = await self.db.execute(
            select(MedicalRecord)
            .where(MedicalRecord.patient_id == patient.id)
            .order_by(desc(MedicalRecord.created_at))
            .limit(10)
        )
        
        medical_records = medical_history_result.scalars().all()
        
        # Format medical history
        medical_history = []
        for record in medical_records:
            medical_history.append({
                "record_id": str(record.id),
                "date": record.created_at.date().isoformat(),
                "chief_complaint": record.chief_complaint,
                "diagnosis": record.diagnosis,
                "treatment_plan": record.treatment_plan,
                "follow_up_instructions": record.follow_up_instructions,
                "vital_signs": record.vital_signs or {}
            })
        
        # Get last visit date
        last_visit_result = await self.db.execute(
            select(Appointment.appointment_date)
            .where(
                and_(
                    Appointment.patient_id == patient.id,
                    Appointment.doctor_id == doctor.id,
                    Appointment.status == AppointmentStatus.COMPLETED
                )
            )
            .order_by(desc(Appointment.appointment_date))
            .limit(1)
        )
        
        last_visit = last_visit_result.scalar_one_or_none()
        
        patient_age = self.calculate_age(patient.date_of_birth)
        
        return {
            "patient_ref": patient.patient_id,
            "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
            "patient_age": patient_age,
            "appointment_ref": current_appointment.appointment_ref if current_appointment else "",
            "appointment_date": current_appointment.appointment_date if current_appointment else "",
            "appointment_time": current_appointment.appointment_time if current_appointment else "",
            "chief_complaint": current_appointment.chief_complaint if current_appointment else None,
            "medical_history": medical_history,
            "current_medications": patient.current_medications or [],
            "allergies": patient.allergies or [],
            "chronic_conditions": patient.chronic_conditions or [],
            "vital_signs": {},  # Will be filled from latest medical record
            "last_visit_date": last_visit
        }
    
    async def create_medical_record(self, record_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Create medical record for patient consultation"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == record_data["appointment_ref"],
                    Appointment.doctor_id == doctor.id
                )
            )
            .options(selectinload(Appointment.patient))
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        # Check if medical record already exists for this appointment
        existing_record = await self.db.execute(
            select(MedicalRecord)
            .where(MedicalRecord.appointment_id == appointment.id)
        )
        
        if existing_record.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Medical record already exists for this appointment"
            )
        
        # Create medical record
        medical_record = MedicalRecord(
            patient_id=appointment.patient_id,
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            hospital_id=uuid.UUID(user_context["hospital_id"]),
            chief_complaint=record_data["chief_complaint"],
            history_of_present_illness=record_data.get("history_of_present_illness"),
            past_medical_history=record_data.get("past_medical_history"),
            examination_findings=record_data.get("examination_findings"),
            vital_signs=record_data.get("vital_signs", {}),
            diagnosis=record_data.get("diagnosis"),
            differential_diagnosis=record_data.get("differential_diagnosis", []),
            treatment_plan=record_data.get("treatment_plan"),
            follow_up_instructions=record_data.get("follow_up_instructions"),
            prescriptions=record_data.get("prescriptions", []),
            lab_orders=record_data.get("lab_orders", []),
            imaging_orders=record_data.get("imaging_orders", []),
            is_finalized=True,
            finalized_at=datetime.now(timezone.utc)
        )
        
        self.db.add(medical_record)
        await self.db.commit()
        await self.db.refresh(medical_record)
        
        return {
            "message": "Medical record created successfully",
            "record_id": str(medical_record.id),
            "patient_ref": appointment.patient.patient_id,
            "appointment_ref": record_data["appointment_ref"],
            "created_at": medical_record.created_at.isoformat()
        }
    
    # ============================================================================
    # PRESCRIPTION MANAGEMENT
    # ============================================================================
    
    async def create_prescription(self, prescription_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Create prescription for patient"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get patient
        patient_result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == prescription_data["patient_ref"],
                    PatientProfile.hospital_id == user_context["hospital_id"]
                )
            )
        )
        
        patient = patient_result.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient not found"
            )
        
        # Get appointment if provided
        appointment_id = None
        medical_record_id = None
        
        if prescription_data.get("appointment_ref"):
            appointment_result = await self.db.execute(
                select(Appointment)
                .where(
                    and_(
                        Appointment.appointment_ref == prescription_data["appointment_ref"],
                        Appointment.doctor_id == doctor.id,
                        Appointment.patient_id == patient.id
                    )
                )
            )
            
            appointment = appointment_result.scalar_one_or_none()
            if appointment:
                appointment_id = appointment.id
                
                # Get associated medical record
                medical_record_result = await self.db.execute(
                    select(MedicalRecord)
                    .where(MedicalRecord.appointment_id == appointment.id)
                )
                
                medical_record = medical_record_result.scalar_one_or_none()
                if medical_record:
                    medical_record_id = medical_record.id
        
        # Generate prescription number
        prescription_number = self.generate_prescription_number()
        
        # Get or create the doctor profile
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Create prescription
        from app.models.doctor import Prescription
        prescription = Prescription(
            patient_id=patient.id,
            doctor_id=doctor_profile.id,  # Use doctor_profile.id instead of doctor.id
            appointment_id=appointment_id,
            medical_record_id=medical_record_id,
            hospital_id=uuid.UUID(user_context["hospital_id"]),
            prescription_number=prescription_number,
            prescription_date=date.today().isoformat(),
            diagnosis=prescription_data.get("diagnosis"),
            symptoms=prescription_data.get("symptoms"),
            medications=prescription_data["medications"],
            general_instructions=prescription_data.get("general_instructions"),
            diet_instructions=prescription_data.get("diet_instructions"),
            follow_up_date=prescription_data.get("follow_up_date"),
            is_digitally_signed=True,  # Assuming digital signature
            signature_hash=f"hash_{prescription_number}"  # Placeholder for actual hash
        )
        
        self.db.add(prescription)
        await self.db.commit()
        await self.db.refresh(prescription)
        
        return {
            "message": "Prescription created successfully",
            "prescription_id": str(prescription.id),
            "prescription_number": prescription_number,
            "patient_ref": prescription_data["patient_ref"],
            "total_medications": len(prescription_data["medications"]),
            "created_at": prescription.created_at.isoformat()
        }
    
    async def get_doctor_prescriptions(self, filters: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Get doctor's prescriptions with filtering options"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get or create the doctor profile
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Build query conditions
        from app.models.doctor import Prescription
        conditions = [Prescription.doctor_id == doctor_profile.id]
        
        if filters.get("patient_ref"):
            # Get patient ID
            patient_result = await self.db.execute(
                select(PatientProfile.id)
                .where(
                    and_(
                        PatientProfile.patient_id == filters["patient_ref"],
                        PatientProfile.hospital_id == user_context["hospital_id"]
                    )
                )
            )
            
            patient_id = patient_result.scalar_one_or_none()
            if patient_id:
                conditions.append(Prescription.patient_id == patient_id)
            else:
                # Patient not found, return empty result
                return {
                    "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
                    "total_prescriptions": 0,
                    "filters": filters,
                    "prescriptions": []
                }
        
        if filters.get("date_from"):
            conditions.append(Prescription.prescription_date >= filters["date_from"])
        
        if filters.get("date_to"):
            conditions.append(Prescription.prescription_date <= filters["date_to"])
        
        # Get prescriptions
        prescriptions_result = await self.db.execute(
            select(Prescription)
            .where(and_(*conditions))
            .options(selectinload(Prescription.patient).selectinload(PatientProfile.user))
            .order_by(desc(Prescription.created_at))
            .limit(filters.get("limit", 20))
        )
        
        prescriptions = prescriptions_result.scalars().all()
        
        # Format prescriptions
        prescription_list = []
        for prescription in prescriptions:
            prescription_list.append({
                "prescription_id": str(prescription.id),
                "prescription_number": prescription.prescription_number,
                "patient_ref": prescription.patient.patient_id,
                "patient_name": f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
                "prescription_date": prescription.prescription_date,
                "diagnosis": prescription.diagnosis,
                "total_medications": len(prescription.medications),
                "medications": prescription.medications,
                "general_instructions": prescription.general_instructions,
                "follow_up_date": prescription.follow_up_date,
                "is_dispensed": prescription.is_dispensed,
                "dispensed_at": prescription.dispensed_at,
                "created_at": prescription.created_at.isoformat()
            })
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "total_prescriptions": len(prescription_list),
            "filters": filters,
            "prescriptions": prescription_list
        }
    
    async def get_prescription_details(self, prescription_number: str, current_user: User) -> Dict[str, Any]:
        """Get detailed prescription information"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get or create the doctor profile
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Get prescription
        from app.models.doctor import Prescription
        prescription_result = await self.db.execute(
            select(Prescription)
            .where(
                and_(
                    Prescription.prescription_number == prescription_number,
                    Prescription.doctor_id == doctor_profile.id  # Use doctor_profile.id
                )
            )
            .options(selectinload(Prescription.patient).selectinload(PatientProfile.user))
        )
        
        prescription = prescription_result.scalar_one_or_none()
        if not prescription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Prescription not found"
            )
        
        return {
            "prescription_id": str(prescription.id),
            "prescription_number": prescription.prescription_number,
            "patient_ref": prescription.patient.patient_id,
            "patient_name": f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "prescription_date": prescription.prescription_date,
            "diagnosis": prescription.diagnosis,
            "symptoms": prescription.symptoms,
            "medications": prescription.medications,
            "general_instructions": prescription.general_instructions,
            "diet_instructions": prescription.diet_instructions,
            "follow_up_date": prescription.follow_up_date,
            "is_dispensed": prescription.is_dispensed,
            "dispensed_at": prescription.dispensed_at,
            "is_digitally_signed": prescription.is_digitally_signed,
            "created_at": prescription.created_at.isoformat()
        }
    
    # ============================================================================
    # PATIENT SEARCH AND LOOKUP
    # ============================================================================
    
    async def search_patients(self, search_query: str, limit: int, current_user: User) -> Dict[str, Any]:
        """Search patients by name, phone, or patient ID"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Search patients
        search_conditions = [
            PatientProfile.hospital_id == user_context["hospital_id"]
        ]
        
        # Add search conditions
        search_terms = or_(
            PatientProfile.patient_id.ilike(f"%{search_query}%"),
            User.first_name.ilike(f"%{search_query}%"),
            User.last_name.ilike(f"%{search_query}%"),
            User.phone.ilike(f"%{search_query}%"),
            User.email.ilike(f"%{search_query}%")
        )
        
        search_conditions.append(search_terms)
        
        # Execute search
        patients_result = await self.db.execute(
            select(PatientProfile)
            .join(User, PatientProfile.user_id == User.id)
            .where(and_(*search_conditions))
            .options(selectinload(PatientProfile.user))
            .limit(limit)
        )
        
        patients = patients_result.scalars().all()
        
        # Format results
        patient_list = []
        for patient in patients:
            patient_age = self.calculate_age(patient.date_of_birth)
            
            # Get last appointment with this doctor
            last_appointment_result = await self.db.execute(
                select(Appointment.appointment_date, Appointment.status)
                .where(
                    and_(
                        Appointment.patient_id == patient.id,
                        Appointment.doctor_id == doctor.id
                    )
                )
                .order_by(desc(Appointment.appointment_date))
                .limit(1)
            )
            
            last_appointment = last_appointment_result.first()
            
            patient_list.append({
                "patient_ref": patient.patient_id,
                "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
                "patient_age": patient_age,
                "phone_number": patient.user.phone,
                "email": patient.user.email,
                "blood_group": patient.blood_group,
                "chronic_conditions": patient.chronic_conditions or [],
                "last_appointment_date": last_appointment.appointment_date if last_appointment else None,
                "last_appointment_status": last_appointment.status if last_appointment else None
            })
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "search_query": search_query,
            "total_results": len(patient_list),
            "patients": patient_list
        }
    
    # ============================================================================
    # STATISTICS AND REPORTS
    # ============================================================================
    
    async def get_statistics_summary(self, period: str, current_user: User) -> Dict[str, Any]:
        """Get comprehensive statistics summary for doctor"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get or create the doctor profile
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Calculate date range
        today = date.today()
        if period == "week":
            start_date = today - timedelta(days=today.weekday())
        elif period == "month":
            start_date = today.replace(day=1)
        elif period == "quarter":
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_start_month, day=1)
        else:  # year
            start_date = today.replace(month=1, day=1)
        
        end_date = today
        
        # Get appointment statistics
        appointments_stats = await self.db.execute(
            select(
                func.count(Appointment.id).label('total'),
                func.count(func.nullif(Appointment.status != AppointmentStatus.COMPLETED, True)).label('completed'),
                func.count(func.nullif(Appointment.status != AppointmentStatus.CANCELLED, True)).label('cancelled'),
                func.count(func.distinct(Appointment.patient_id)).label('unique_patients')
            )
            .where(
                and_(
                    Appointment.doctor_id == doctor.id,
                    Appointment.appointment_date >= start_date.isoformat(),
                    Appointment.appointment_date <= end_date.isoformat()
                )
            )
        )
        
        apt_stats = appointments_stats.first()
        
        # Get prescription statistics
        from app.models.doctor import Prescription
        prescription_stats = await self.db.execute(
            select(func.count(Prescription.id))
            .where(
                and_(
                    Prescription.doctor_id == doctor_profile.id,  # Use doctor_profile.id
                    Prescription.prescription_date >= start_date.isoformat(),
                    Prescription.prescription_date <= end_date.isoformat()
                )
            )
        )
        
        total_prescriptions = prescription_stats.scalar() or 0
        
        # Get medical record statistics
        medical_record_stats = await self.db.execute(
            select(func.count(MedicalRecord.id))
            .where(
                and_(
                    MedicalRecord.doctor_id == doctor.id,
                    MedicalRecord.created_at >= datetime.combine(start_date, datetime.min.time()),
                    MedicalRecord.created_at <= datetime.combine(end_date, datetime.max.time())
                )
            )
        )
        
        total_medical_records = medical_record_stats.scalar() or 0
        
        # Calculate completion rate
        total_appointments = apt_stats.total or 0
        completed_appointments = apt_stats.completed or 0
        completion_rate = round((completed_appointments / total_appointments * 100) if total_appointments > 0 else 0, 1)
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "period": period,
            "date_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat()
            },
            "statistics": {
                "appointments": {
                    "total": total_appointments,
                    "completed": completed_appointments,
                    "cancelled": apt_stats.cancelled or 0,
                    "completion_rate": completion_rate
                },
                "patients": {
                    "unique_patients_treated": apt_stats.unique_patients or 0
                },
                "prescriptions": {
                    "total_prescriptions": total_prescriptions
                },
                "medical_records": {
                    "total_records_created": total_medical_records
                }
            }
        }
    
    # ============================================================================
    # HELPER METHODS
    # ============================================================================
    
    def calculate_age(self, date_of_birth: str) -> int:
        """Calculate age from date of birth"""
        try:
            birth_date = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
            today = date.today()
            return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        except:
            return 0
    
    def generate_prescription_number(self) -> str:
        """Generate unique prescription number"""
        import random
        import string
        
        # Format: RX-YYYY-XXXXXX
        year = datetime.now().year
        random_part = ''.join(random.choices(string.digits, k=6))
        return f"RX-{year}-{random_part}"
    
    async def add_prescription_items(self, prescription_id: str, items_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Add medicines and lab orders to an existing prescription"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Get prescription
        from app.models.doctor import Prescription
        prescription_result = await self.db.execute(
            select(Prescription)
            .where(
                and_(
                    Prescription.id == uuid.UUID(prescription_id),
                    Prescription.doctor_id == doctor_profile.id
                )
            )
        )
        
        prescription = prescription_result.scalar_one_or_none()
        if not prescription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Prescription not found"
            )
        
        medicines_added = 0
        lab_orders_added = 0
        
        # Process medicines
        if items_data.get("medicines"):
            for medicine_data in items_data["medicines"]:
                # Validate medicine ID if provided
                if medicine_data.get("medicine_id") and medicine_data["medicine_id"].strip():
                    is_valid, error_message = validate_medicine_id(medicine_data["medicine_id"])
                    
                    if is_valid:
                        try:
                            medicine_uuid = uuid.UUID(medicine_data["medicine_id"])
                            # Validate medicine exists in the database
                            from app.models.medicine import Medicine
                            medicine_result = await self.db.execute(
                                select(Medicine)
                                .where(
                                    and_(
                                        Medicine.id == medicine_uuid,
                                        Medicine.hospital_id == user_context["hospital_id"]
                                    )
                                )
                            )
                            
                            medicine = medicine_result.scalar_one_or_none()
                            if not medicine:
                                raise HTTPException(
                                    status_code=status.HTTP_400_BAD_REQUEST,
                                    detail={
                                        "code": "INVALID_MEDICINE_ID",
                                        "message": f"Medicine with ID {medicine_data['medicine_id']} not found"
                                    }
                                )
                        except ValueError:
                            # This shouldn't happen since we validated above, but handle gracefully
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail={
                                    "code": "INVALID_MEDICINE_ID",
                                    "message": error_message or "Invalid medicine ID format"
                                }
                            )
                    else:
                        # Invalid medicine ID format
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={
                                "code": "INVALID_MEDICINE_ID",
                                "message": error_message or "Invalid medicine ID format"
                            }
                        )
                
                medicines_added += 1
        
        # Process lab orders
        if items_data.get("lab_orders"):
            lab_orders_added = len(items_data["lab_orders"])
        
        # For now, return success without actually modifying the prescription
        # In a full implementation, this would update the prescription's medications array
        
        return {
            "message": "Prescription items processed successfully",
            "prescription_id": prescription_id,
            "medicines_added": medicines_added,
            "lab_orders_added": lab_orders_added,
            "validation_notes": "Medicine IDs validated where provided"
        }