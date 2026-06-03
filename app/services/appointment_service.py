"""
Appointment Service for multi-tenant Hospital Management SaaS.
Handles appointment booking with human-readable IDs and department-doctor flow.
"""
import uuid
from datetime import datetime, timedelta, time
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.patient import Appointment, PatientProfile
from app.models.doctor import DoctorProfile
from app.models.schedule import DoctorSchedule
from app.models.hospital import Department, StaffDepartmentAssignment
from app.models.user import User
from app.core.enums import AppointmentStatus, UserRole
from app.core.utils import generate_appointment_ref, generate_patient_ref
from app.utils.hospital_id_resolve import resolve_effective_hospital_id
from app.utils.receptionist_serializers import build_receptionist_patient_full_payload
from app.database.session import get_tenant_session_factory
from app.database.tenant_context import resolve_tenant_database_name_for_hospital

DEFAULT_APPOINTMENT_SLOT_MINUTES = 30


def _normalize_appointment_time_parts(appointment_time: str) -> tuple[str, str]:
    """Return (HH:MM for slot match, HH:MM:SS for DB)."""
    raw = (appointment_time or "").strip()
    parts = [p for p in raw.split(":") if p != ""]
    if len(parts) >= 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) >= 2:
        h, m, s = int(parts[0]), int(parts[1]), 0
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid appointment_time; use HH:MM or HH:MM:SS",
        )
    return f"{h:02d}:{m:02d}", f"{h:02d}:{m:02d}:{s:02d}"


class AppointmentService:
    """Service for managing appointments with hospital isolation"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_departments(self, hospital_id: str) -> List[Dict[str, Any]]:
        """Get all departments in the hospital"""
        result = await self.db.execute(
            select(Department)
            .where(
                Department.hospital_id == hospital_id,
                Department.is_active == True
            )
            .order_by(Department.name)
        )
        departments = result.scalars().all()
        
        return [
            {
                "id": str(dept.id),
                "name": dept.name,
                "description": dept.description,
                "head_of_department": dept.head_of_department
            }
            for dept in departments
        ]
    
    async def get_doctors_by_department(self, department_id: str, hospital_id: str) -> List[Dict[str, Any]]:
        """Get all doctors in a specific department (profile link or staff assignment)."""
        dept_uuid = uuid.UUID(str(department_id))
        hid = uuid.UUID(str(hospital_id))
        assigned_staff = (
            select(StaffDepartmentAssignment.staff_id)
            .where(
                and_(
                    StaffDepartmentAssignment.hospital_id == hid,
                    StaffDepartmentAssignment.department_id == dept_uuid,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
            .distinct()
        )
        result = await self.db.execute(
            select(DoctorProfile)
            .join(User, DoctorProfile.user_id == User.id)
            .where(
                DoctorProfile.hospital_id == hid,
                User.is_active == True,
                or_(
                    DoctorProfile.department_id == dept_uuid,
                    DoctorProfile.user_id.in_(assigned_staff),
                ),
            )
            .options(selectinload(DoctorProfile.user))
            .order_by(User.first_name, User.last_name)
        )
        doctors = result.scalars().all()
        
        return [
            {
                "id": str(doctor.user_id),
                "user_id": str(doctor.user_id),
                "doctor_profile_id": str(doctor.id),
                "doctor_id": doctor.doctor_id,
                "name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
                "designation": doctor.designation,
                "specialization": doctor.specialization,
                "consultation_fee": float(doctor.consultation_fee),
                "experience_years": doctor.experience_years,
                "is_accepting_patients": doctor.is_accepting_new_patients
            }
            for doctor in doctors
        ]
    
    async def get_available_time_slots_for_doctor_user(
        self,
        doctor_user_id: uuid.UUID,
        date: str,
        exclude_appointment_id: Optional[uuid.UUID] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build bookable time slots for a doctor (User.id) on a date from DoctorSchedule only.
        No default hours — if the doctor has no active schedule row for that weekday, returns [].
        """
        target_date = datetime.fromisoformat(date)
        day_of_week = target_date.strftime("%A").upper()

        doctor_schedule = await self._get_schedule_for_doctor_day(
            doctor_user_id,
            day_of_week,
            target_date.date().isoformat(),
        )
        if not doctor_schedule:
            await self._sync_doctor_schedules_from_tenant(doctor_user_id)
            doctor_schedule = await self._get_schedule_for_doctor_day(
                doctor_user_id,
                day_of_week,
                target_date.date().isoformat(),
            )
        if not doctor_schedule:
            return []

        # Simplified schedule creation hides slot length; use a stable internal default.
        slot_mins = doctor_schedule.slot_duration_minutes or DEFAULT_APPOINTMENT_SLOT_MINUTES
        if slot_mins is None or slot_mins < 15:
            return []

        slots: List[Dict[str, Any]] = []
        current_time = datetime.combine(target_date.date(), doctor_schedule.start_time)
        end_boundary = datetime.combine(target_date.date(), doctor_schedule.end_time)
        slot_duration = timedelta(minutes=slot_mins)
        max_patients = max(1, doctor_schedule.max_patients_per_slot or 1)

        while current_time + slot_duration <= end_boundary:
            t = current_time.time()
            if (
                doctor_schedule.break_start_time
                and doctor_schedule.break_end_time
                and doctor_schedule.break_start_time <= t < doctor_schedule.break_end_time
            ):
                current_time += slot_duration
                continue

            if target_date.date() == datetime.now().date() and current_time <= datetime.now():
                current_time += slot_duration
                continue

            time_hms = current_time.strftime("%H:%M:%S")
            booked_filt = and_(
                Appointment.doctor_id == doctor_user_id,
                Appointment.appointment_date == date,
                Appointment.appointment_time == time_hms,
                Appointment.status.in_([AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED]),
            )
            if exclude_appointment_id is not None:
                booked_filt = and_(booked_filt, Appointment.id != exclude_appointment_id)
            booked_q = await self.db.execute(select(func.count(Appointment.id)).where(booked_filt))
            booked = int(booked_q.scalar() or 0)
            is_available = booked < max_patients

            slots.append(
                {
                    "time": current_time.strftime("%H:%M"),
                    "time_24h": time_hms,
                    "is_available": is_available,
                    "duration_minutes": int(slot_mins),
                }
            )
            current_time += slot_duration

        return slots

    async def _get_schedule_for_doctor_day(
        self,
        doctor_user_id: uuid.UUID,
        day_of_week: str,
        date_iso: str,
    ) -> Optional[DoctorSchedule]:
        """Find a schedule by canonical User.id, tolerating old rows stored with DoctorProfile.id."""
        profile_ids: list[uuid.UUID] = []
        profile_result = await self.db.execute(
            select(DoctorProfile.id).where(DoctorProfile.user_id == doctor_user_id)
        )
        profile_ids = list(profile_result.scalars().all())
        doctor_ids = [doctor_user_id, *profile_ids]

        result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.doctor_id.in_(doctor_ids),
                    func.upper(func.trim(DoctorSchedule.day_of_week)) == day_of_week,
                    DoctorSchedule.is_active == True,
                    or_(DoctorSchedule.effective_from.is_(None), DoctorSchedule.effective_from <= date_iso),
                    or_(DoctorSchedule.effective_to.is_(None), DoctorSchedule.effective_to >= date_iso),
                )
            )
            .order_by(
                (DoctorSchedule.doctor_id == doctor_user_id).desc(),
                (DoctorSchedule.effective_from == date_iso).desc(),
                DoctorSchedule.created_at.desc(),
            )
            .limit(1)
        )
        schedule = result.scalar_one_or_none()
        if schedule and schedule.doctor_id != doctor_user_id:
            # Repair legacy Hospital Admin rows where DoctorProfile.id was saved as DoctorSchedule.doctor_id.
            schedule.doctor_id = doctor_user_id
            await self.db.flush()
        return schedule

    async def _sync_doctor_schedules_from_tenant(self, doctor_user_id: uuid.UUID) -> None:
        """
        Receptionist OPD scheduling reads platform DB, while doctor/admin schedule writes can be tenant-routed.
        Lazily copy the doctor's schedule rows into this session, correcting legacy profile-id rows.
        """
        doctor = await self.db.get(User, doctor_user_id)
        hospital_id = getattr(doctor, "hospital_id", None)
        if not hospital_id:
            return

        try:
            from app.database.session import get_tenant_session_factory
            from app.database.tenant_context import resolve_tenant_database_name_for_hospital

            tenant_db = await resolve_tenant_database_name_for_hospital(hospital_id)
            if not tenant_db:
                return

            tenant_rows: list[dict[str, Any]] = []
            fac = get_tenant_session_factory(tenant_db)
            async with fac() as tdb:
                profile_result = await tdb.execute(
                    select(DoctorProfile.id).where(DoctorProfile.user_id == doctor_user_id)
                )
                tenant_profile_ids = list(profile_result.scalars().all())
                doctor_ids = [doctor_user_id, *tenant_profile_ids]
                schedule_result = await tdb.execute(
                    select(DoctorSchedule).where(DoctorSchedule.doctor_id.in_(doctor_ids))
                )
                for schedule in schedule_result.scalars().all():
                    row = {
                        col.name: getattr(schedule, col.name)
                        for col in DoctorSchedule.__table__.columns
                    }
                    row["doctor_id"] = doctor_user_id
                    tenant_rows.append(row)

            for row in tenant_rows:
                existing = await self.db.get(DoctorSchedule, row["id"])
                if existing:
                    for key, value in row.items():
                        if key != "id":
                            setattr(existing, key, value)
                else:
                    self.db.add(DoctorSchedule(**row))
            if tenant_rows:
                await self.db.flush()
        except Exception:
            try:
                await self.db.rollback()
            except Exception:
                pass
            # Availability endpoints should keep returning the normal empty-slot response on sync failure.
            return

    async def get_doctor_available_slots(
        self,
        doctor_id: str,
        date: str,
        hospital_id: str,
    ) -> List[Dict[str, Any]]:
        """Get available time slots for a doctor profile (DoctorProfile.id) on a specific date."""
        result = await self.db.execute(
            select(DoctorProfile)
            .where(
                DoctorProfile.id == doctor_id,
                DoctorProfile.hospital_id == hospital_id,
            )
            .options(selectinload(DoctorProfile.user))
        )
        doctor = result.scalar_one_or_none()

        if not doctor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor not found",
            )

        return await self.get_available_time_slots_for_doctor_user(doctor.user_id, date)
    
    async def create_appointment(
        self,
        patient_user_id: str,
        department_id: str,
        doctor_id: str,
        appointment_date: str,
        appointment_time: str,
        chief_complaint: str,
        hospital_id: str
    ) -> Dict[str, Any]:
        """Create a new appointment"""
        
        # Get patient profile (initially global; hospital will be assigned here if missing)
        patient_result = await self.db.execute(
            select(PatientProfile)
            .where(PatientProfile.user_id == patient_user_id)
            .options(selectinload(PatientProfile.user))
        )
        patient = patient_result.scalar_one_or_none()
        
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient profile not found. Please complete your profile first."
            )

        # If patient has no hospital yet, bind them to the selected hospital now.
        # If they already have a hospital, enforce that it matches the appointment hospital.
        if patient.hospital_id is None:
            patient.hospital_id = hospital_id
            # Also update the user's hospital_id
            if patient.user and patient.user.hospital_id is None:
                patient.user.hospital_id = uuid.UUID(hospital_id)
        elif str(patient.hospital_id) != str(hospital_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient is already registered with a different hospital."
            )
        
        # Get doctor profile
        doctor_result = await self.db.execute(
            select(DoctorProfile)
            .where(
                DoctorProfile.id == doctor_id,
                DoctorProfile.hospital_id == hospital_id
            )
            .options(selectinload(DoctorProfile.user))
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if not doctor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor not found"
            )
        
        time_hhmm, time_hhmmss = _normalize_appointment_time_parts(appointment_time)

        # Validate appointment date (not in the past)
        appointment_datetime = datetime.fromisoformat(f"{appointment_date}T{time_hhmmss}")
        if appointment_datetime <= datetime.now():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot book appointments in the past"
            )

        # Book only times generated from this doctor's schedule (duration from schedule, not a default)
        day_slots = await self.get_available_time_slots_for_doctor_user(doctor.user_id, appointment_date)
        if not day_slots:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This doctor has no availability on that day. Choose a date with a published schedule.",
            )
        slot_match = next((s for s in day_slots if s["time"] == time_hhmm), None)
        if not slot_match:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected time is not in this doctor's schedule. Use available slots from the booking API.",
            )
        if not slot_match.get("is_available"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This time slot is already booked",
            )
        duration_minutes = int(slot_match["duration_minutes"])

        # Check if slot is available (race safety; doctor_id is User.id)
        existing_appointment = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.doctor_id == doctor.user_id,
                    Appointment.appointment_date == appointment_date,
                    Appointment.appointment_time == time_hhmmss,
                    Appointment.status.in_([AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED])
                )
            )
        )
        
        if existing_appointment.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This time slot is already booked"
            )
        
        # Generate unique appointment reference
        appointment_ref = generate_appointment_ref()
        
        # Ensure appointment_ref is unique
        while True:
            existing = await self.db.execute(
                select(Appointment).where(Appointment.appointment_ref == appointment_ref)
            )
            if not existing.scalar_one_or_none():
                break
            appointment_ref = generate_appointment_ref()
        
        # Create appointment (doctor_id references users.id, same as DoctorSchedule)
        # Create appointment (doctor_id references users.id, same as DoctorSchedule)

                # Generate unique appointment reference
        appointment_ref = generate_appointment_ref()

        # Ensure appointment_ref is unique
        while True:
            existing = await self.db.execute(
                select(Appointment).where(
                    Appointment.appointment_ref == appointment_ref
                )
            )

            if not existing.scalar_one_or_none():
                break

            appointment_ref = generate_appointment_ref()

        # Resolve tenant database
        tenant_db_name = await resolve_tenant_database_name_for_hospital(
            str(hospital_id)
        )

        if not tenant_db_name:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Tenant database not configured"
            )

        tenant_session_factory = get_tenant_session_factory(
            tenant_db_name
        )

        # Create appointment in tenant DB
        async with tenant_session_factory() as tenant_db:

            appointment = Appointment(
                appointment_ref=appointment_ref,
                patient_id=patient.id,
                doctor_id=doctor.user_id,
                department_id=department_id,
                hospital_id=hospital_id,
                appointment_date=appointment_date,
                appointment_time=time_hhmmss,
                duration_minutes=duration_minutes,
                status=AppointmentStatus.REQUESTED,
                chief_complaint=chief_complaint,
                consultation_fee=doctor.consultation_fee,
                created_by_role=UserRole.PATIENT,
                created_by_user=patient_user_id
            )

            tenant_db.add(appointment)
            await tenant_db.commit()
            await tenant_db.refresh(appointment)

        return {
            "appointment_id": appointment_ref,
            "patient_id": patient.patient_id,
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "status": appointment.status,
            "consultation_fee": float(appointment.consultation_fee),
            "message": "Appointment booked successfully! Please arrive 15 minutes early."
        }
        # appointment = Appointment(
        #     appointment_ref=appointment_ref,
        #     patient_id=patient.id,
        #     doctor_id=doctor.user_id,
        #     department_id=department_id,
        #     hospital_id=hospital_id,
        #     appointment_date=appointment_date,
        #     appointment_time=time_hhmmss,
        #     duration_minutes=duration_minutes,
        #     status=AppointmentStatus.REQUESTED,
        #     chief_complaint=chief_complaint,
        #     consultation_fee=doctor.consultation_fee,
        #     created_by_role=UserRole.PATIENT,
        #     created_by_user=patient_user_id
        # )
        
        # self.db.add(appointment)
        # await self.db.commit()
        # await self.db.refresh(appointment)
        
        # return {
        #     "appointment_id": appointment_ref,
        #     "patient_id": patient.patient_id,
        #     "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        #     "appointment_date": appointment_date,
        #     "appointment_time": appointment_time,
        #     "status": appointment.status,
        #     "consultation_fee": float(appointment.consultation_fee),
        #     "message": "Appointment booked successfully! Please arrive 15 minutes early."
        # }
    
    async def get_patient_appointments(
        self,
        patient_user_id: str,
        hospital_id: str,
        status_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get appointments for a patient"""
        
        # Get patient profile
        patient_result = await self.db.execute(
            select(PatientProfile)
            .where(
                PatientProfile.user_id == patient_user_id,
                PatientProfile.hospital_id == hospital_id
            )
        )
        patient = patient_result.scalar_one_or_none()
        
        if not patient:
            return []
        
        # Build query
        query = select(Appointment).where(
            and_(
                Appointment.patient_id == patient.id,
                Appointment.hospital_id == hospital_id
            )
        ).options(
            selectinload(Appointment.doctor).selectinload(DoctorProfile.user),
            selectinload(Appointment.department)
        ).order_by(Appointment.appointment_date.desc(), Appointment.appointment_time.desc())
        
        if status_filter:
            query = query.where(Appointment.status == status_filter)
        
        result = await self.db.execute(query)
        appointments = result.scalars().all()
        
        return [
            {
                "appointment_id": apt.appointment_ref,
                "doctor_name": f"Dr. {apt.doctor.user.first_name} {apt.doctor.user.last_name}",
                "department": apt.department.name,
                "appointment_date": apt.appointment_date,
                "appointment_time": apt.appointment_time,
                "status": apt.status,
                "chief_complaint": apt.chief_complaint,
                "consultation_fee": float(apt.consultation_fee) if apt.consultation_fee else 0,
                "created_at": apt.created_at.isoformat()
            }
            for apt in appointments
        ]

    async def search_patients(
        self,
        search_params: Dict[str, Any],
        current_user: User
    ) -> Dict[str, Any]:
        """Search patients by phone, email, name, patient_id, or MRN (receptionist)."""
        hospital_id = current_user.hospital_id or await resolve_effective_hospital_id(
            self.db, current_user
        )
        if not hospital_id:
            return {"patients": [], "total": 0, "page": 1, "limit": 20}
        query = select(PatientProfile).join(User, PatientProfile.user_id == User.id).where(
            PatientProfile.hospital_id == hospital_id
        )
        generic = (search_params.get("search") or search_params.get("q") or "").strip()
        if generic:
            term = f"%{generic}%"
            full_name = func.lower(
                func.trim(
                    func.concat(
                        func.coalesce(User.first_name, ""),
                        " ",
                        func.coalesce(User.last_name, ""),
                    )
                )
            )
            query = query.where(
                or_(
                    User.first_name.ilike(term),
                    User.last_name.ilike(term),
                    full_name.ilike(f"%{generic.lower()}%"),
                    User.email.ilike(term),
                    User.phone.ilike(term),
                    PatientProfile.patient_id.ilike(term),
                    PatientProfile.mrn.ilike(term),
                )
            )
        if search_params.get("phone"):
            query = query.where(User.phone.ilike(f"%{search_params['phone']}%"))
        if search_params.get("email"):
            query = query.where(User.email.ilike(f"%{search_params['email']}%"))
        if search_params.get("name"):
            raw = search_params["name"].strip()
            term = f"%{raw}%"
            full_name = func.lower(
                func.trim(
                    func.concat(
                        func.coalesce(User.first_name, ""),
                        " ",
                        func.coalesce(User.last_name, ""),
                    )
                )
            )
            query = query.where(
                or_(
                    User.first_name.ilike(term),
                    User.last_name.ilike(term),
                    full_name.ilike(f"%{raw.lower()}%"),
                )
            )
        if search_params.get("patient_id"):
            query = query.where(PatientProfile.patient_id.ilike(f"%{search_params['patient_id']}%"))
        if search_params.get("mrn"):
            query = query.where(PatientProfile.mrn.ilike(f"%{search_params['mrn']}%"))
        page = search_params.get("page", 1)
        limit = min(search_params.get("limit", 20), 100)
        offset = (page - 1) * limit
        count_query = select(func.count(PatientProfile.id)).select_from(PatientProfile).join(User, PatientProfile.user_id == User.id).where(PatientProfile.hospital_id == hospital_id)
        if generic:
            term = f"%{generic}%"
            full_name = func.lower(
                func.trim(
                    func.concat(
                        func.coalesce(User.first_name, ""),
                        " ",
                        func.coalesce(User.last_name, ""),
                    )
                )
            )
            count_query = count_query.where(
                or_(
                    User.first_name.ilike(term),
                    User.last_name.ilike(term),
                    full_name.ilike(f"%{generic.lower()}%"),
                    User.email.ilike(term),
                    User.phone.ilike(term),
                    PatientProfile.patient_id.ilike(term),
                    PatientProfile.mrn.ilike(term),
                )
            )
        if search_params.get("phone"):
            count_query = count_query.where(User.phone.ilike(f"%{search_params['phone']}%"))
        if search_params.get("email"):
            count_query = count_query.where(User.email.ilike(f"%{search_params['email']}%"))
        if search_params.get("name"):
            raw = search_params["name"].strip()
            term = f"%{raw}%"
            full_name = func.lower(
                func.trim(
                    func.concat(
                        func.coalesce(User.first_name, ""),
                        " ",
                        func.coalesce(User.last_name, ""),
                    )
                )
            )
            count_query = count_query.where(
                or_(
                    User.first_name.ilike(term),
                    User.last_name.ilike(term),
                    full_name.ilike(f"%{raw.lower()}%"),
                )
            )
        if search_params.get("patient_id"):
            count_query = count_query.where(PatientProfile.patient_id.ilike(f"%{search_params['patient_id']}%"))
        if search_params.get("mrn"):
            count_query = count_query.where(PatientProfile.mrn.ilike(f"%{search_params['mrn']}%"))
        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0
        result = await self.db.execute(
            query.order_by(desc(PatientProfile.created_at))
            .offset(offset)
            .limit(limit)
            .options(selectinload(PatientProfile.user))
        )
        patients = result.scalars().all()
        return {
            "patients": [build_receptionist_patient_full_payload(p) for p in patients],
            "total": total,
            "page": page,
            "limit": limit,
        }

    async def get_appointment_statistics(
        self,
        date_str: Optional[str],
        current_user: User
    ) -> Dict[str, Any]:
        """Get appointment statistics for a date (receptionist)."""
        from datetime import date as date_type
        hospital_id = current_user.hospital_id
        if not hospital_id:
            return {"total": 0, "by_status": {}, "date": date_str or str(date_type.today())}
        target_date = date_str or str(date_type.today())
        result = await self.db.execute(
            select(Appointment.status, func.count(Appointment.id))
            .where(
                and_(
                    Appointment.hospital_id == hospital_id,
                    Appointment.appointment_date == target_date
                )
            )
            .group_by(Appointment.status)
        )
        rows = result.all()
        by_status = {row[0]: row[1] for row in rows}
        total = sum(by_status.values())
        return {
            "date": target_date,
            "total": total,
            "by_status": by_status,
        }