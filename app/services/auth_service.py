import logging
logger = logging.getLogger(__name__)
"""
Authentication service for multi-tenant hospital management system.
Handles user registration, login, password management, and OTP verification.

UPDATED RULES:
- Only patients can register (self-registration)
- Hospital Admin/Staff are created by system (no registration)
- Hospital Admin/Staff must use hospital-approved email domains
- Patients can use any email domain
"""
import asyncio
import re
import secrets
import uuid
import string
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from fastapi import HTTPException, status

from app.models.user import User, Role, user_roles
from app.models.tenant import Hospital
from app.models.patient import PatientProfile
from app.models.password_history import PasswordHistory
from app.core.security import SecurityManager
from app.core.config import settings
from app.database.session import invalidate_hospital_tenant_cache
from app.services.email_service import EmailService
from app.services.otp_service import otp_service
from app.core.enums import UserRole, UserStatus
from app.core.enums import HospitalStatus, SubscriptionStatus


class EmailDomainValidator:
    """Validates email domains for different user types"""
    
    # Public domains not allowed for hospital staff
    PUBLIC_DOMAINS = {
        'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 
        'aol.com', 'icloud.com', 'protonmail.com', 'mail.com'
    }
    
    @staticmethod
    def is_hospital_approved_domain(email: str, hospital_domains: List[str]) -> bool:
        """Check if email domain is approved for hospital staff"""
        domain = email.split('@')[1].lower() if '@' in email else ''
        return domain in [d.lower() for d in hospital_domains]
    
    @staticmethod
    def is_public_domain(email: str) -> bool:
        """Check if email uses a public domain"""
        domain = email.split('@')[1].lower() if '@' in email else ''
        return domain in EmailDomainValidator.PUBLIC_DOMAINS
    
    @staticmethod
    def validate_staff_email(email: str, hospital_domains: List[str]) -> Dict[str, Any]:
        """Validate email for hospital staff (Admin/Doctor/Pharmacist/Lab Tech)"""
        if EmailDomainValidator.is_public_domain(email):
            return {
                'valid': False,
                'error': f"Hospital staff cannot use public email domains. Please use your hospital email address."
            }
        
        if not EmailDomainValidator.is_hospital_approved_domain(email, hospital_domains):
            approved_domains = ', '.join(hospital_domains)
            return {
                'valid': False,
                'error': f"Email domain not approved for this hospital. Approved domains: {approved_domains}"
            }
        
        return {'valid': True, 'error': None}


class PasswordValidator:
    """Validates password according to security rules"""
    
    @staticmethod
    def validate_password(password: str, email: str = "", phone: str = "") -> Dict[str, Any]:
        """Validate password against security rules"""
        errors = []
        
        # Length check
        if len(password) < 8:
            errors.append("Password must be at least 8 characters long")
        
        # Character requirements
        if not re.search(r'[A-Z]', password):
            errors.append("Password must contain at least one uppercase letter")
        
        if not re.search(r'[a-z]', password):
            errors.append("Password must contain at least one lowercase letter")
        
        if not re.search(r'\d', password):
            errors.append("Password must contain at least one number")
        
        if not re.search(r'[@#$%!]', password):
            errors.append("Password must contain at least one special character (@#$%!)")
        
        # No spaces
        if ' ' in password:
            errors.append("Password cannot contain spaces")
        
        # Cannot contain email or phone
        if email and email.lower() in password.lower():
            errors.append("Password cannot contain your email address")
        
        if phone and phone in password:
            errors.append("Password cannot contain your phone number")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors
        }


class AuthService:
    """Authentication service for multi-tenant system"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.security = SecurityManager()
        self.email_service = EmailService()
        # Use global OTP service so generated codes are visible across requests
        self.otp_service = otp_service
    
    # SYSTEM USER CREATION METHODS (NEW)
    
    async def create_hospital(self, hospital_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create hospital by Super Admin"""
        from app.models.tenant import Hospital
        
        logger.debug(f"DEBUG: Database session: {self.db}")
        logger.debug(f"DEBUG: Hospital data: {hospital_data}")
        
        # Check if hospital already exists (by registration number).
        # If it exists but is inactive (soft-deleted), reactivate it so old staff/admin
        # credentials continue to work once subscription is assigned again.
        existing_hospital_result = await self.db.execute(
            select(Hospital).where(Hospital.registration_number == hospital_data["registration_number"])
        )
        existing_hospital = existing_hospital_result.scalar_one_or_none()
        if existing_hospital:
            if not getattr(existing_hospital, "is_active", True) or getattr(existing_hospital, "status", None) == HospitalStatus.INACTIVE:
                existing_hospital.name = hospital_data["name"]
                existing_hospital.email = hospital_data["email"]
                existing_hospital.phone = hospital_data["phone"]
                existing_hospital.address = hospital_data["address"]
                existing_hospital.city = hospital_data["city"]
                existing_hospital.state = hospital_data["state"]
                existing_hospital.country = hospital_data["country"]
                existing_hospital.pincode = hospital_data["pincode"]
                existing_hospital.status = HospitalStatus.ACTIVE
                existing_hospital.is_active = True

                # Preserve existing settings but ensure the hospital domain is present.
                existing_settings = existing_hospital.settings or {}
                approved = list(existing_settings.get("approved_email_domains", []) or [])
                domain = hospital_data["email"].split("@")[1]
                if domain.lower() not in [d.lower() for d in approved]:
                    approved.append(domain)
                existing_settings["approved_email_domains"] = approved
                existing_hospital.settings = existing_settings

                if settings.TENANT_DB_AUTO_PROVISION and not existing_hospital.tenant_database_name:
                    from app.services.tenant_database_provisioning import (
                        bootstrap_tenant_database,
                        tenant_db_name_for_hospital,
                        provision_postgres_database,
                        tenant_provision_http_detail,
                    )

                    used_template = bool((settings.TENANT_TEMPLATE_DATABASE or "").strip())
                    tdb = tenant_db_name_for_hospital(existing_hospital.id, existing_hospital.name)
                    try:
                        await asyncio.to_thread(provision_postgres_database, tdb, None)
                    except Exception as e:
                        logger.exception("Tenant DB provision failed on reactivation")
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=tenant_provision_http_detail(e, reactivate=True),
                        ) from e
                    existing_hospital.tenant_database_name = tdb
                    await self.db.flush()
                    await asyncio.to_thread(
                        bootstrap_tenant_database, tdb, existing_hospital, used_template
                    )

                await self.db.commit()
                invalidate_hospital_tenant_cache(existing_hospital.id)
                return {
                    "hospital_id": str(existing_hospital.id),
                    "name": existing_hospital.name,
                    "registration_number": existing_hospital.registration_number,
                    "tenant_database_name": existing_hospital.tenant_database_name,
                    "message": "Hospital reactivated successfully",
                }

            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "HOSPITAL_EXISTS", "message": "Hospital with this registration number already exists"},
            )
        
        # Create hospital (dedicated Postgres DB on same server when enabled)
        hospital_id = uuid.uuid4()
        tenant_db: Optional[str] = None
        used_template = bool((settings.TENANT_TEMPLATE_DATABASE or "").strip())
        if settings.TENANT_DB_AUTO_PROVISION:
            from app.services.tenant_database_provisioning import (
                tenant_db_name_for_hospital,
                provision_postgres_database,
                tenant_provision_http_detail,
            )

            tenant_db = tenant_db_name_for_hospital(hospital_id, hospital_data.get("name"))
            try:
                await asyncio.to_thread(provision_postgres_database, tenant_db, None)
            except Exception as e:
                logger.exception("Tenant DB provision failed on create_hospital")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=tenant_provision_http_detail(e),
                ) from e

        hospital = Hospital(
            id=hospital_id,
            name=hospital_data['name'],
            registration_number=hospital_data['registration_number'],
            email=hospital_data['email'],
            phone=hospital_data['phone'],
            address=hospital_data['address'],
            city=hospital_data['city'],
            state=hospital_data['state'],
            country=hospital_data['country'],
            pincode=hospital_data['pincode'],
            tenant_database_name=tenant_db,
            settings={
                "approved_email_domains": [hospital_data['email'].split('@')[1]]  # Auto-approve hospital's domain
            }
        )
        
        logger.debug(f"DEBUG: Creating hospital: {hospital.name} with ID: {hospital.id}")
        self.db.add(hospital)
        logger.debug(f"DEBUG: Hospital added to session")
        if tenant_db:
            from app.services.tenant_database_provisioning import bootstrap_tenant_database

            await self.db.flush()
            await asyncio.to_thread(bootstrap_tenant_database, tenant_db, hospital, used_template)
        await self.db.commit()
        invalidate_hospital_tenant_cache(hospital.id)
        logger.debug(f"DEBUG: Hospital committed to database")
        
        # Verify hospital was saved
        saved_hospital = await self._get_hospital_by_id(hospital.id)
        logger.debug(f"DEBUG: Verified hospital in DB: {saved_hospital}")
        
        return {
            "hospital_id": str(hospital.id),
            "name": hospital.name,
            "registration_number": hospital.registration_number,
            "tenant_database_name": hospital.tenant_database_name,
            "message": "Hospital created successfully",
        }
    
    async def create_hospital_admin(self, hospital_id: uuid.UUID, admin_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create hospital admin by Super Admin (system-generated password)"""
        # Debug: List all hospitals
        all_hospitals = await self.db.execute(select(Hospital))
        hospitals = all_hospitals.scalars().all()
        logger.debug(f"DEBUG: All hospitals in database: {[(h.id, h.name) for h in hospitals]}")
        
        # Get hospital to check approved domains
        hospital = await self._get_hospital_by_id(hospital_id)
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Get hospital approved domains
        hospital_domains = hospital.settings.get('approved_email_domains', [])
        if not hospital_domains:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NO_APPROVED_DOMAINS", "message": "Hospital has no approved email domains configured"}
            )
        
        # Validate email domain for hospital admin
        email_validation = EmailDomainValidator.validate_staff_email(admin_data['email'], hospital_domains)
        if not email_validation['valid']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_EMAIL_DOMAIN", "message": email_validation['error']}
            )
        
        # Check if user already exists
        existing_user = await self._get_user_by_email(admin_data['email'])
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "USER_EXISTS", "message": "User with this email already exists"}
            )
        
        # Validate password provided by Super Admin
        password_validation = PasswordValidator.validate_password(
            admin_data['password'],
            admin_data['email'],
            admin_data.get('phone', '')
        )
        
        if not password_validation['valid']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "PWD_001",
                    "message": "Password does not meet security requirements",
                    "errors": password_validation['errors']
                }
            )
        
        # Use the password provided by Super Admin
        admin_password = admin_data['password']
        logger.debug(f"DEBUG: Using Super Admin provided password")
        
        # Create hospital admin
        user = User(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            email=admin_data['email'].lower(),
            phone=admin_data['phone'],
            first_name=admin_data['first_name'],
            last_name=admin_data['last_name'],
            password_hash=self.security.hash_password(admin_password),
            status=UserStatus.ACTIVE,  # No email verification needed
            email_verified=True  # System-created users are pre-verified
        )
        logger.debug(f"DEBUG: Created user: {user.email}")
        
        self.db.add(user)
        await self.db.flush()
        logger.debug(f"DEBUG: User added to database with ID: {user.id}")
        
        # Assign hospital admin role
        admin_role = await self._get_role_by_name(UserRole.HOSPITAL_ADMIN)
        if not admin_role:
            # Create HOSPITAL_ADMIN role if it doesn't exist
            admin_role = Role(
                id=uuid.uuid4(),
                name=UserRole.HOSPITAL_ADMIN,
                display_name="Hospital Administrator",
                description="Hospital Administrator Role",
                level=50
            )
            self.db.add(admin_role)
            await self.db.flush()
        
        # Add role to user using direct SQL to avoid lazy loading issues
        await self.db.execute(
            user_roles.insert().values(user_id=user.id, role_id=admin_role.id)
        )
        
        await self.db.commit()
        
        result = {
            "user_id": str(user.id),
            "email": user.email,
            "message": "Hospital admin created successfully."
        }
        logger.debug(f"DEBUG: Returning result: {result}")
        return result
    
    async def create_hospital_staff(self, staff_data: Dict[str, Any], creator_hospital_id: uuid.UUID) -> Dict[str, Any]:
        """Create hospital staff by Hospital Admin (system-generated password)"""
        # Get hospital to check approved domains
        hospital = await self._get_hospital_by_id(creator_hospital_id)
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Get hospital approved domains
        hospital_domains = hospital.settings.get('approved_email_domains', [])
        if not hospital_domains:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NO_APPROVED_DOMAINS", "message": "Hospital has no approved email domains configured"}
            )
        
        # Validate email domain for staff
        email_validation = EmailDomainValidator.validate_staff_email(staff_data['email'], hospital_domains)
        if not email_validation['valid']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_EMAIL_DOMAIN", "message": email_validation['error']}
            )
        
        # Check if user already exists
        existing_user = await self._get_user_by_email(staff_data['email'])
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "USER_EXISTS", "message": "User with this email already exists"}
            )
        
        # Validate role
        allowed_staff_roles = [UserRole.DOCTOR, UserRole.PHARMACIST, UserRole.LAB_TECH, UserRole.NURSE, UserRole.RECEPTIONIST]
        if staff_data['role'] not in allowed_staff_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ROLE", "message": f"Invalid role. Allowed roles: {', '.join(allowed_staff_roles)}"}
            )
        
        # Validate password provided by Hospital Admin
        password_validation = PasswordValidator.validate_password(
            staff_data['password'],
            staff_data['email'],
            staff_data.get('phone', '')
        )
        
        if not password_validation['valid']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "PWD_001",
                    "message": "Password does not meet security requirements",
                    "errors": password_validation['errors']
                }
            )
        
        # Generate system password
        system_password = self._generate_system_password()
        
        # Create staff user
        user = User(
            id=uuid.uuid4(),
            hospital_id=creator_hospital_id,
            email=staff_data['email'].lower(),
            phone=staff_data['phone'],
            first_name=staff_data['first_name'],
            last_name=staff_data['last_name'],
            password_hash=self.security.hash_password(staff_data['password']),
            status=UserStatus.ACTIVE,  # No email verification needed
            email_verified=True  # System-created users are pre-verified
        )
        
        self.db.add(user)
        await self.db.flush()
        
        # Assign role
        role = await self._get_role_by_name(staff_data['role'])
        if not role:
            # Create role if it doesn't exist
            role = Role(
                id=uuid.uuid4(),
                name=staff_data['role'],
                display_name=staff_data['role'].replace('_', ' ').title(),
                description=f"{staff_data['role']} Role",
                level=10
            )
            self.db.add(role)
            await self.db.flush()
        
        # Add role to user using direct SQL to avoid lazy loading issues
        await self.db.execute(
            user_roles.insert().values(user_id=user.id, role_id=role.id)
        )
        
        await self.db.commit()
        
        return {
            "user_id": str(user.id),
            "email": user.email,
            "role": staff_data['role'],
            "message": "Hospital staff created successfully."
        }
    
    # PATIENT REGISTRATION (EXISTING - ONLY PATIENTS CAN REGISTER)
    
    async def register_patient(self, registration_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new patient with email verification (ONLY patients can register)"""
        # Validate password
        password_validation = PasswordValidator.validate_password(
            registration_data['password'],
            registration_data['email'],
            registration_data.get('phone', '')
        )
        
        if not password_validation['valid']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "PWD_001",
                    "message": "Password does not meet security requirements",
                    "errors": password_validation['errors']
                }
            )
        
        # Check if user already exists
        existing_user = await self._get_user_by_email(registration_data['email'])
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "AUTH_007", "message": "User with this email already exists"}
            )

        # Resolve hospital for patient registration; hospital_id is assigned ONLY during registration.
        hospital = await self._resolve_hospital_for_patient(registration_data)
        hospital_id = hospital.id if hospital else None

        user = User(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            email=registration_data['email'].lower(),
            phone=registration_data['phone'],
            first_name=registration_data['first_name'],
            last_name=registration_data['last_name'],
            password_hash=self.security.hash_password(registration_data['password']),
            status=UserStatus.PENDING,
            email_verified=False
        )
        
        self.db.add(user)
        await self.db.flush()
        
        # Assign patient role
        patient_role = await self._get_role_by_name(UserRole.PATIENT)
        if not patient_role:
            # Create PATIENT role if it doesn't exist
            patient_role = Role(
                id=uuid.uuid4(),
                name=UserRole.PATIENT,
                display_name="Patient",
                description="Patient Role",
                level=1
            )
            self.db.add(patient_role)
            await self.db.flush()
        
        # Add role to user using direct SQL to avoid lazy loading issues
        await self.db.execute(
            user_roles.insert().values(user_id=user.id, role_id=patient_role.id)
        )
        
        # Create patient profile
        from app.core.utils import generate_patient_ref
        patient_ref = generate_patient_ref()
        
        # Ensure patient_ref is unique
        while True:
            existing_ref = await self.db.execute(
                select(PatientProfile).where(PatientProfile.patient_id == patient_ref)
            )
            if not existing_ref.scalar_one_or_none():
                break
            patient_ref = generate_patient_ref()
        
        # Parse date of birth if provided
        date_of_birth = None
        if registration_data.get('date_of_birth'):
            try:
                from app.core.utils import parse_date_string
                date_obj = parse_date_string(registration_data['date_of_birth'])
                # Convert datetime back to string format for database storage (YYYY-MM-DD)
                date_of_birth = date_obj.strftime('%Y-%m-%d') if date_obj else None
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_DATE_FORMAT",
                        "message": "Invalid date of birth format. Use YYYY-MM-DD or other standard formats."
                    }
                )
        
        patient_profile = PatientProfile(
            id=uuid.uuid4(),
            user_id=user.id,
            hospital_id=hospital_id,
            patient_id=patient_ref,
            date_of_birth=date_of_birth,
            gender=registration_data.get('gender'),
            address=registration_data.get('address'),
            emergency_contact_name=registration_data.get('emergency_contact_name'),
            emergency_contact_phone=registration_data.get('emergency_contact_phone')
        )
        
        self.db.add(patient_profile)
        
        # Generate and send OTP
        otp_code = await self.otp_service.generate_otp(user.email, "email_verification")
        await self.email_service.send_verification_email(user.email, otp_code, user.first_name)
        
        await self.db.commit()
        
        result = {
            "user_id": str(user.id),
            "patient_id": patient_ref,
            "email": user.email,
            "status": "pending_verification",
            "message": "Registration successful. Please check your email for verification code.",
        }
        if hospital:
            result["hospital_id"] = str(hospital.id)
            result["hospital_name"] = hospital.name
        return result
    
    # LOGIN METHODS (SPECIFIC FOR EACH USER TYPE)
    
    async def super_admin_login(self, email: str, password: str) -> Dict[str, Any]:
        """Super Admin login - no OTP required"""
        logger.debug(f"DEBUG: Super Admin login attempt for email: {email}")
        
        # Get user
        user = await self._get_user_by_email(email)
        logger.debug(f"DEBUG: Found user: {user}")
        if not user:
            logger.debug("DEBUG: User not found")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )
        
        # Check if user has SUPER_ADMIN role
        user_roles = [role.name for role in user.roles]
        logger.debug(f"DEBUG: User roles: {user_roles}")
        if "SUPER_ADMIN" not in user_roles:
            logger.debug("DEBUG: User does not have SUPER_ADMIN role")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH_002", "message": "Super Admin access required"}
            )
        
        # Check password
        logger.debug(f"DEBUG: Checking password")
        if not self.security.verify_password(password, user.password_hash):
            logger.debug("DEBUG: Password verification failed")
            await self._log_failed_login(user.id, "invalid_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )
        
        logger.debug("DEBUG: Login successful, generating tokens")
        # Generate tokens
        return await self._generate_auth_response(user)

    async def _enforce_hospital_login_access(self, user: User, user_roles: List[str]) -> None:
        """
        Enforce hospital active status + subscription for hospital-scoped (non-patient) users.

        Rules:
        - SUPER_ADMIN bypasses all checks.
        - If user has a hospital_id:
            - Hospital must exist and be active (not soft-deleted).
            - Hospital must have an ACTIVE, non-expired subscription.
            - If subscription is missing/expired/suspended/cancelled -> block login.
        """
        if "SUPER_ADMIN" in (user_roles or []):
            return

        if not user.hospital_id:
            return

        from app.models.tenant import HospitalSubscription, Hospital
        from datetime import datetime as _dt, timezone as _tz

        hosp_result = await self.db.execute(
            select(Hospital).where(Hospital.id == user.hospital_id)
        )
        hospital = hosp_result.scalar_one_or_none()
        if not hospital or not hospital.is_active or hospital.status == HospitalStatus.INACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "HOSPITAL_INACTIVE",
                    "message": "Hospital is inactive. Please contact support.",
                },
            )

        sub_result = await self.db.execute(
            select(HospitalSubscription).where(HospitalSubscription.hospital_id == user.hospital_id)
        )
        subscription = sub_result.scalar_one_or_none()
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "SUBSCRIPTION_REQUIRED",
                    "message": "Subscription plan required to login.",
                },
            )

        # Compare timestamps safely (timezone-aware vs naive).
        # DB DateTime(timezone=True) may return aware datetimes; older rows may be naive.
        now = _dt.now(_tz.utc)
        if subscription.end_date:
            end_dt = subscription.end_date
            if getattr(end_dt, "tzinfo", None) is None:
                end_dt = end_dt.replace(tzinfo=_tz.utc)
            else:
                end_dt = end_dt.astimezone(_tz.utc)
            if end_dt < now:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail={
                        "code": "SUBSCRIPTION_EXPIRED",
                        "message": f"Subscription expired on {end_dt.strftime('%Y-%m-%d')}. Renew to continue.",
                    },
                )

        if subscription.status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED):
            status_str = str(subscription.status)
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": f"SUBSCRIPTION_{status_str}",
                    "message": f"Subscription is {status_str.lower()}.",
                },
            )
    
    async def hospital_admin_login(self, email: str, password: str) -> Dict[str, Any]:
        """Hospital Admin login - no OTP required"""
        logger.debug(f"DEBUG: Hospital Admin login attempt for email: {email}")
        
        # Get user
        user = await self._get_user_by_email(email)
        logger.debug(f"DEBUG: Found user: {user}")
        
        if not user:
            logger.debug("DEBUG: User not found")
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials"
            )
        
        # Check if user has HOSPITAL_ADMIN role
        user_roles = [role.name for role in user.roles]
        logger.debug(f"DEBUG: User roles: {user_roles}")

        if "HOSPITAL_ADMIN" not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH_002", "message": "Hospital Admin access required"}
            )
        
        # Check password
        logger.debug(f"DEBUG: Checking password")
        password_valid = self.security.verify_password(password, user.password_hash)
        logger.debug(f"DEBUG: Password valid: {password_valid}")

        if not password_valid:
            await self._log_failed_login(user.id, "invalid_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )

        # Account must be active (hospital delete blocks users).
        if user.status != UserStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "USER_INACTIVE", "message": "Account is inactive. Contact your administrator."},
            )

        # Hospital + subscription gating (covers new hospitals too).
        await self._enforce_hospital_login_access(user, user_roles)
        
        logger.debug("DEBUG: Login successful, generating tokens")
        # Generate tokens
        return await self._generate_auth_response(user)
    
    async def staff_login(self, email: str, password: str) -> Dict[str, Any]:
        """Staff login - no OTP required"""
        # Get user
        user = await self._get_user_by_email(email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )
        
        # Check if user has staff role
        user_roles = [role.name for role in user.roles]
        staff_roles = ["DOCTOR", "NURSE", "RECEPTIONIST", "PHARMACIST", "LAB_TECH"]
        if not any(role in user_roles for role in staff_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH_002", "message": "Staff access required"}
            )
        
        # Check password
        if not self.security.verify_password(password, user.password_hash):
            await self._log_failed_login(user.id, "invalid_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )

        if user.status != UserStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "USER_INACTIVE", "message": "Account is inactive. Contact your administrator."},
            )

        await self._enforce_hospital_login_access(user, user_roles)
        
        # Generate tokens
        return await self._generate_auth_response(user)
    
    async def staff_admin_super_admin_login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Unified login for:
        - SUPER_ADMIN
        - HOSPITAL_ADMIN
        - Hospital staff roles (DOCTOR, NURSE, RECEPTIONIST, PHARMACIST, LAB_TECH)

        Patients must use the dedicated patient login endpoint.
        """
        normalized_email = (email or "").strip().lower()
        user = await self._get_user_by_email(normalized_email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )

        user_roles = [role.name for role in user.roles] if user.roles else []

        staff_roles = ["DOCTOR", "NURSE", "RECEPTIONIST", "PHARMACIST", "LAB_TECH"]
        has_allowed_role = (
            "SUPER_ADMIN" in user_roles
            or "HOSPITAL_ADMIN" in user_roles
            or any(role in user_roles for role in staff_roles)
        )

        if not has_allowed_role:
            if "PATIENT" in user_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "AUTH_002", "message": "Patient accounts must use patient login"}
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH_002", "message": "Access denied for this account type"}
            )

        if not self.security.verify_password(password, user.password_hash):
            await self._log_failed_login(user.id, "invalid_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )

        # Block non-active accounts (hospital users are soft-blocked when hospital is deleted/deactivated).
        if user.status != UserStatus.ACTIVE and "SUPER_ADMIN" not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "USER_INACTIVE", "message": "Account is inactive. Contact your administrator."},
            )

        # Enforce hospital active + subscription for hospital-scoped logins
        # (covers new hospitals too).
        await self._enforce_hospital_login_access(user, user_roles)

        return await self._generate_auth_response(user)
    
    async def patient_login(self, email: str, password: str) -> Dict[str, Any]:
        """Patient login - requires email verification"""
        normalized_email = (email or "").strip().lower()
        user = await self._get_user_by_email(normalized_email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )
        
        # Check if user has PATIENT role
        user_roles = [role.name for role in user.roles]
        if "PATIENT" not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH_002", "message": "Patient access required"}
            )
        
        # Check if email is verified (patients only)
        if not user.email_verified:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_003", "message": "Email verification required"}
            )
        
        # Check password
        if not self.security.verify_password(password, user.password_hash):
            await self._log_failed_login(user.id, "invalid_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )
        
        # Generate tokens
        return await self._generate_auth_response(user)
    
    async def _generate_auth_response(self, user) -> Dict[str, Any]:
        """Generate authentication response with tokens"""
        # Create JWT payload (defensive: tolerate missing roles/permissions on inconsistent seed data).
        roles = list(getattr(user, "roles", None) or [])
        user_roles = [str(getattr(role, "name", "")).strip() for role in roles if getattr(role, "name", None)]
        user_permissions: List[str] = []
        for role in roles:
            for permission in (getattr(role, "permissions", None) or []):
                pname = getattr(permission, "name", None)
                if pname:
                    user_permissions.append(str(pname))
        
        payload = {
            "sub": str(user.id),  # Use "sub" as per JWT standard
            "email": user.email,
            "hospital_id": str(user.hospital_id) if user.hospital_id else None,
            "roles": user_roles,
            "permissions": user_permissions
        }
        
        # Generate tokens
        access_token = self.security.create_access_token(payload)
        refresh_token = self.security.create_refresh_token(payload)
        
        # Update last login
        user.last_login = datetime.utcnow()
        await self.db.commit()
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": 1800,  # 30 minutes
            "user": {
                "id": str(user.id),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "roles": user_roles,
                "hospital_id": str(user.hospital_id) if user.hospital_id else None
            }
        }

    # ORIGINAL LOGIN METHOD (KEPT FOR BACKWARD COMPATIBILITY)
    
    async def login(self, email: str, password: str) -> Dict[str, Any]:
        """Authenticate user (different rules for staff vs patients)"""
        # Get user
        user = await self._get_user_by_email(email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )
        
        # Check password
        if not self.security.verify_password(password, user.password_hash):
            await self._log_failed_login(user.id, "invalid_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_001", "message": "Invalid credentials"}
            )
        
        # Check account status
        if user.status != UserStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_002", "message": "Account not verified or inactive"}
            )
        
        # Check email verification ONLY for patients
        if not user.email_verified and self._is_patient(user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_002", "message": "Please verify your email before logging in"}
            )
        
        # Generate tokens
        user_roles = [role.name for role in user.roles]
        user_permissions = []
        for role in user.roles:
            for permission in role.permissions:
                user_permissions.append(permission.name)
        
        token_data = {
            "sub": str(user.id),
            "hospital_id": str(user.hospital_id),
            "roles": user_roles,
            "permissions": user_permissions
        }
        
        access_token = self.security.create_access_token(token_data)
        refresh_token = self.security.create_refresh_token({"sub": str(user.id)})
        
        # Update last login
        user.last_login = datetime.utcnow()
        await self.db.commit()
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "id": str(user.id),
                "email": user.email,
                "name": f"{user.first_name} {user.last_name}",
                "roles": user_roles,
                "hospital_id": str(user.hospital_id)
            }
        }
    
    # EMAIL VERIFICATION (ONLY FOR PATIENTS)
    
    async def verify_email(self, email: str, otp_code: str) -> Dict[str, Any]:
        """Verify email with OTP code (only for patients)"""
        logger.debug(f"Email verification attempt for: {email}")
        
        # Verify OTP code first
        otp_valid = await self.otp_service.verify_otp(email, otp_code, "email_verification")
        if not otp_valid:
            logger.warning(f"Invalid or expired OTP for email verification: {email}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "AUTH_010", "message": "Invalid or expired verification code"}
            )
        
        # Then look up user
        user = await self._get_user_by_email(email)
        if not user:
            logger.warning(f"User not found during email verification for email: {email}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "AUTH_009", "message": "User not found"}
            )
        
        user.status = UserStatus.ACTIVE
        user.email_verified = True
        await self.db.commit()
        
        return {"message": "Email verified successfully. You can now log in.", "status": "verified"}
    
    # PASSWORD RESET (FOR ALL USERS - STAFF USE THIS TO CHANGE PASSWORDS)
    
    async def forgot_password(self, email: str) -> Dict[str, Any]:
        """Send password reset OTP to user email"""
        user = await self._get_user_by_email(email)
        if not user:
            return {"message": "If the email exists, a password reset code has been sent."}
        
        # Generate and send OTP
        otp_code = await self.otp_service.generate_otp(email, "password_reset")
        await self.email_service.send_password_reset_email(email, otp_code, user.first_name)
        
        return {"message": "If the email exists, a password reset code has been sent."}
    
    async def reset_password(self, email: str, otp_code: str, new_password: str) -> Dict[str, Any]:
        """Reset password with OTP verification"""
        if not await self.otp_service.verify_otp(email, otp_code, "password_reset"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "AUTH_008", "message": "Invalid or expired reset code"}
            )
        
        user = await self._get_user_by_email(email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "AUTH_009", "message": "User not found"}
            )
        
        # Validate new password
        password_validation = PasswordValidator.validate_password(new_password, user.email, user.phone)
        if not password_validation['valid']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PWD_001", "message": "Password does not meet security requirements", "errors": password_validation['errors']}
            )
        
        # Check password history
        if await self._is_password_reused(user.id, new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PWD_002", "message": "Cannot reuse recent passwords"}
            )
        
        # Update password
        new_password_hash = self.security.hash_password(new_password)
        user.password_hash = new_password_hash
        user.password_changed_at = datetime.utcnow()
        
        # Save to password history
        await self._save_password_history(user.id, new_password_hash)
        await self.db.commit()
        
        return {"message": "Password reset successfully"}
    
    async def change_password(self, user_id: uuid.UUID, current_password: str, new_password: str) -> Dict[str, Any]:
        """Change password for authenticated user"""
        user = await self._get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "AUTH_009", "message": "User not found"}
            )
        
        # Verify current password
        if not self.security.verify_password(current_password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PWD_004", "message": "Current password is incorrect"}
            )
        
        # Validate new password
        password_validation = PasswordValidator.validate_password(new_password, user.email, user.phone)
        if not password_validation['valid']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PWD_001", "message": "Password does not meet security requirements", "errors": password_validation['errors']}
            )
        
        # Check password history
        if await self._is_password_reused(user.id, new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PWD_002", "message": "Cannot reuse recent passwords"}
            )
        
        # Update password
        new_password_hash = self.security.hash_password(new_password)
        user.password_hash = new_password_hash
        user.password_changed_at = datetime.utcnow()
        
        # Save to password history
        await self._save_password_history(user.id, new_password_hash)
        await self.db.commit()
        
        return {"message": "Password changed successfully"}
    
    async def validate_super_admin_access(self, user: User) -> None:
        """Validate that user has Super Admin access"""
        user_roles = [role.name for role in user.roles]
        if UserRole.SUPER_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "Super Admin access required", "errors": ["Insufficient permissions"]}
            )
    
    async def validate_hospital_admin_access(self, user: User) -> None:
        """Validate that user has Hospital Admin access"""
        user_roles = [role.name for role in user.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "Hospital Admin access required", "errors": ["Insufficient permissions"]}
            )
    
    async def validate_staff_access(self, user: User) -> None:
        """Validate that user has Staff access"""
        user_roles = [role.name for role in user.roles]
        staff_roles = [UserRole.DOCTOR, UserRole.NURSE, UserRole.RECEPTIONIST, UserRole.PHARMACIST, UserRole.LAB_TECH]
        if not any(role in user_roles for role in staff_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "Staff access required", "errors": ["Insufficient permissions"]}
            )
    
    async def validate_patient_access(self, user: User) -> None:
        """Validate that user has Patient access"""
        user_roles = [role.name for role in user.roles]
        if UserRole.PATIENT not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "Patient access required", "errors": ["Insufficient permissions"]}
            )

    async def get_available_hospitals(self) -> List[Dict[str, Any]]:
        """Get list of available hospitals for patient registration"""
        result = await self.db.execute(
            select(Hospital)
            .where(Hospital.is_active == True)
            .order_by(Hospital.name)
        )
        hospitals = result.scalars().all()
        
        hospital_list = []
        for hospital in hospitals:
            hospital_list.append({
                "id": str(hospital.id),
                "name": hospital.name,
                "city": hospital.city,
                "state": hospital.state,
                "phone": hospital.phone,
                "email": hospital.email,
                "address": hospital.address,
                "full_address": f"{hospital.address or ''}, {hospital.city or ''}, {hospital.state or ''}".strip(", "),
            })
        
        return hospital_list
    
    async def get_current_user_info(self, user: User) -> Dict[str, Any]:
        """Get current authenticated user information"""
        user_roles = [role.name for role in user.roles]
        user_permissions = []
        for role in user.roles:
            for permission in role.permissions:
                user_permissions.append(permission.name)
        
        return {
            "id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "status": user.status,
            "email_verified": getattr(user, "email_verified", False) or False,
            "hospital_id": str(user.hospital_id) if user.hospital_id else None,
            "roles": user_roles,
            "permissions": user_permissions,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "created_at": (user.created_at.isoformat() if getattr(user, "created_at", None) else datetime.utcnow().isoformat()),
        }

    # HELPER METHODS
    
    def _generate_system_password(self) -> str:
        """Generate secure system password for hospital admin/staff"""
        chars = string.ascii_letters + string.digits + "@#$%!"
        password = ''.join(secrets.choice(chars) for _ in range(12))
        
        # Ensure it meets password policy
        while not PasswordValidator.validate_password(password)['valid']:
            password = ''.join(secrets.choice(chars) for _ in range(12))
        
        return password
    
    async def _get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email with roles loaded (case-insensitive; trims stored email)."""
        from sqlalchemy.orm import selectinload

        normalized_email = (email or "").strip().lower()
        result = await self.db.execute(
            select(User)
            .options(selectinload(User.roles).selectinload(Role.permissions))
            .where(func.lower(func.trim(User.email)) == normalized_email)
        )
        return result.scalar_one_or_none()
    
    async def _get_user_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        """Get user by ID with roles loaded"""
        from sqlalchemy.orm import selectinload
        result = await self.db.execute(
            select(User)
            .options(selectinload(User.roles).selectinload(Role.permissions))
            .where(User.id == user_id)
        )
        return result.scalar_one_or_none()
    
    async def _get_role_by_name(self, role_name: str) -> Optional[Role]:
        """Get role by name"""
        result = await self.db.execute(select(Role).where(Role.name == role_name))
        return result.scalar_one_or_none()
    
    async def _get_hospital_by_id(self, hospital_id: uuid.UUID) -> Optional[Hospital]:
        """Get hospital by ID"""
        logger.debug(f"DEBUG: Looking for hospital with ID: {hospital_id}")
        result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        hospital = result.scalar_one_or_none()
        logger.debug(f"DEBUG: Found hospital: {hospital}")
        return hospital
    
    async def _resolve_hospital_for_patient(self, registration_data: Dict[str, Any]) -> Hospital:
        """
        Resolve hospital for patient registration.

        Behavior:
        - If hospital_id is provided: load that active hospital by UUID.
        - If hospital_id is omitted: auto-resolve when a single sensible default exists (see below).
        """
        raw_hid = registration_data.get("hospital_id")
        hospital_id: Optional[uuid.UUID] = None
        if raw_hid is not None and raw_hid != "":
            if isinstance(raw_hid, uuid.UUID):
                hospital_id = raw_hid
            else:
                try:
                    hospital_id = uuid.UUID(str(raw_hid).strip())
                except (ValueError, TypeError):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "AUTH_012",
                            "message": "Invalid hospital_id; must be a UUID (use GET /api/v1/auth/hospitals).",
                        },
                    )

        if hospital_id is not None:
            hospital = await self._get_hospital_by_id(hospital_id)
            if not hospital or not getattr(hospital, "is_active", True):
                available = await self.db.execute(
                    select(Hospital.id, Hospital.name)
                    .where(Hospital.is_active == True)
                    .order_by(Hospital.name)
                )
                choices = [{"id": str(rid), "name": rname} for rid, rname in available.fetchall()]
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "AUTH_013",
                        "message": "Hospital not found or inactive for the given hospital_id",
                        "available_hospitals": choices,
                    },
                )
            return hospital

        # No hospital_id provided: try to auto-resolve
        result = await self.db.execute(
            select(Hospital).where(Hospital.is_active == True).order_by(Hospital.name)
        )
        hospitals = result.scalars().all()

        if not hospitals:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "AUTH_011",
                    "message": "No active hospitals are configured for patient registration",
                },
            )

        if len(hospitals) > 1:
            # Multiple hospitals exist – but registration flow should not force the user
            # to choose a hospital. Auto-select a sensible default so that the API
            # does not block on hospital selection.
            # Prefer a platform/default hospital if present, otherwise use the first.
            preferred = next((h for h in hospitals if h.name == "Platform Hospital"), None)
            return preferred or hospitals[0]

        # Exactly one active hospital – safe to auto-assign
        return hospitals[0]
    
    def _is_patient(self, user: User) -> bool:
        """Check if user has patient role"""
        return any(
            str(getattr(role, "name", "")).strip() == UserRole.PATIENT.value
            for role in (getattr(user, "roles", None) or [])
        )
    
    async def _log_failed_login(self, user_id: uuid.UUID, reason: str):
        """Log failed login attempt"""
        pass  # Implement audit logging
    
    async def _is_password_reused(self, user_id: uuid.UUID, new_password: str) -> bool:
        """Check if password was used recently (last 3 passwords)"""
        result = await self.db.execute(
            select(PasswordHistory)
            .where(PasswordHistory.user_id == user_id)
            .order_by(PasswordHistory.created_at.desc())
            .limit(3)
        )
        recent_passwords = result.scalars().all()
        
        for password_record in recent_passwords:
            if self.security.verify_password(new_password, password_record.password_hash):
                return True
        
        return False
    
    async def _save_password_history(self, user_id: uuid.UUID, password_hash: str):
        """Save password to history"""
        password_history = PasswordHistory(user_id=user_id, password_hash=password_hash)
        self.db.add(password_history)
        
        # Keep only last 5 passwords in history
        result = await self.db.execute(
            select(PasswordHistory)
            .where(PasswordHistory.user_id == user_id)
            .order_by(PasswordHistory.created_at.desc())
            .offset(5)
        )
        old_passwords = result.scalars().all()
        
        for old_password in old_passwords:
            await self.db.delete(old_password)