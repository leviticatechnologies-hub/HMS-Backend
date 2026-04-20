"""
Super Admin service for platform-level administrative operations.
Handles hospital management, subscription control, analytics, and compliance monitoring.
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc, asc
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
import re
import random
import string

from app.models.user import User, Role, AuditLog
from app.models.tenant import Hospital, SubscriptionPlanModel, HospitalSubscription
from app.core.enums import UserRole, UserStatus, SubscriptionStatus, SubscriptionPlan, AuditAction, HospitalStatus
from app.core.security import SecurityManager
from app.core.utils import parse_date_string

logger = logging.getLogger(__name__)


def generate_staff_id(role: str, department_name: str, first_name: str, last_name: str) -> str:
    """
    Generate a 10-character staff ID like an ID card.
    Format: [ROLE][DEPT][NAME][NUM]
    
    Examples:
    - DR-CARD-JS01 (Doctor, Cardiology, John Smith)
    - NR-ORTH-SJ02 (Nurse, Orthopedics, Sarah Johnson)
    - RC-EMER-MK03 (Receptionist, Emergency, Mike Kumar)
    """
    # Role codes (2 chars)
    role_codes = {
        "DOCTOR": "DR",
        "NURSE": "NR", 
        "RECEPTIONIST": "RC",
        "PHARMACIST": "PH",
        "LAB_TECH": "LT"
    }
    
    # Get role code
    role_code = role_codes.get(role.upper(), "ST")  # Default to ST (Staff)
    
    # Department code (4 chars) - take first 4 chars of department name
    dept_code = re.sub(r'[^A-Z]', '', department_name.upper())[:4]
    if len(dept_code) < 4:
        dept_code = dept_code.ljust(4, 'X')
    
    # Name code (2 chars) - first char of first name + first char of last name
    name_code = (first_name[0] + last_name[0]).upper()
    
    # Random number (2 chars)
    random_num = f"{random.randint(1, 99):02d}"
    
    # Combine: DR-CARD-JS01 (10 chars with dashes, 8 chars without)
    staff_id = f"{role_code}{dept_code}{name_code}{random_num}"
    
    return staff_id


class SuperAdminService:
    """Service class for Super Admin operations"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.security = SecurityManager()
    
    # ============================================================================
    # HOSPITAL MANAGEMENT
    # ============================================================================
    
    async def get_hospitals(
        self, 
        page: int = 1, 
        limit: int = 50,
        status_filter: Optional[str] = None,
        subscription_filter: Optional[str] = None,
        city_filter: Optional[str] = None,
        state_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated list of hospitals with filtering"""
        offset = (page - 1) * limit
        
        # Build query with filters
        query = select(Hospital).options(
            selectinload(Hospital.subscription).selectinload(HospitalSubscription.plan)
        )
        
        # Apply filters
        conditions = []
        if status_filter:
            sf = (status_filter or "").strip().upper()
            if sf in (HospitalStatus.ACTIVE.value, HospitalStatus.SUSPENDED.value, HospitalStatus.INACTIVE.value):
                conditions.append(Hospital.status == sf)
            elif sf in ("TRUE", "1", "YES"):
                conditions.append(Hospital.is_active.is_(True))
            elif sf in ("FALSE", "0", "NO"):
                conditions.append(Hospital.is_active.is_(False))

        if subscription_filter:
            query = query.join(HospitalSubscription, HospitalSubscription.hospital_id == Hospital.id).join(
                SubscriptionPlanModel, SubscriptionPlanModel.id == HospitalSubscription.plan_id
            )
            conditions.append(SubscriptionPlanModel.name == subscription_filter)

        if city_filter:
            conditions.append(Hospital.city.ilike(f"%{city_filter}%"))

        if state_filter:
            conditions.append(Hospital.state.ilike(f"%{state_filter}%"))

        if conditions:
            query = query.where(and_(*conditions))

        # Total count (must use same joins as main query when subscription filter is applied)
        count_query = select(func.count(Hospital.id)).select_from(Hospital)
        if subscription_filter:
            count_query = count_query.join(
                HospitalSubscription, HospitalSubscription.hospital_id == Hospital.id
            ).join(SubscriptionPlanModel, SubscriptionPlanModel.id == HospitalSubscription.plan_id)
        if conditions:
            count_query = count_query.where(and_(*conditions))
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.offset(offset).limit(limit).order_by(Hospital.created_at.desc())
        result = await self.db.execute(query)
        hospitals = result.scalars().all()
        
        # Format response
        hospital_list = []
        for hospital in hospitals:
            subscription_status = None
            subscription_plan = None
            
            if hospital.subscription:
                subscription_status = hospital.subscription.status
                subscription_plan = hospital.subscription.plan.name if hospital.subscription.plan else None
            
            hospital_list.append({
                "id": str(hospital.id),
                "name": hospital.name,
                "email": hospital.email,
                "tenant_database_name": hospital.tenant_database_name,
                "phone": hospital.phone,
                "contact": hospital.phone,
                "address": hospital.address,
                "city": hospital.city,
                "state": hospital.state,
                "country": hospital.country,
                "pincode": hospital.pincode,
                "registration_number": hospital.registration_number,
                "subscription_status": subscription_status,
                "subscription_plan": subscription_plan,
                "created_at": hospital.created_at.isoformat(),
                "is_active": bool(hospital.is_active),
                "status": hospital.status,
            })
        
        return {
            "hospitals": hospital_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }
    
    async def get_hospital_details(self, hospital_id: uuid.UUID) -> Dict[str, Any]:
        """Get detailed hospital information"""
        # Get hospital with subscription details
        query = select(Hospital).options(
            selectinload(Hospital.subscription).selectinload(HospitalSubscription.plan)
        ).where(Hospital.id == hospital_id)
        
        result = await self.db.execute(query)
        hospital = result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Get hospital admin count
        admin_count_query = select(func.count(User.id)).where(
            and_(
                User.hospital_id == hospital_id,
                User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN.value),
            )
        )
        admin_count_result = await self.db.execute(admin_count_query)
        admin_count = admin_count_result.scalar() or 0
        
        # Get total user count
        user_count_query = select(func.count(User.id)).where(User.hospital_id == hospital_id)
        user_count_result = await self.db.execute(user_count_query)
        user_count = user_count_result.scalar() or 0
        
        # Format subscription details
        subscription_details = None
        if hospital.subscription:
            subscription_details = {
                "plan_name": hospital.subscription.plan.name if hospital.subscription.plan else None,
                "plan_display_name": hospital.subscription.plan.display_name if hospital.subscription.plan else None,
                "status": hospital.subscription.status,
                "start_date": hospital.subscription.start_date.isoformat(),
                "end_date": hospital.subscription.end_date.isoformat(),
                "is_trial": hospital.subscription.is_trial,
                "auto_renew": hospital.subscription.auto_renew,
                "current_usage": hospital.subscription.current_usage
            }
        
        return {
            "id": str(hospital.id),
            "name": hospital.name,
            "registration_number": hospital.registration_number,
            "email": hospital.email,
            "tenant_database_name": hospital.tenant_database_name,
            "phone": hospital.phone,
            "contact": hospital.phone,
            "address": hospital.address,
            "city": hospital.city,
            "state": hospital.state,
            "country": hospital.country,
            "pincode": hospital.pincode,
            "license_number": hospital.license_number,
            "established_date": hospital.established_date.isoformat() if hospital.established_date else None,
            "website": hospital.website,
            "logo_url": hospital.logo_url,
            "status": hospital.status,
            "is_active": bool(hospital.is_active),
            "settings": hospital.settings,
            "created_at": hospital.created_at.isoformat(),
            "updated_at": hospital.updated_at.isoformat(),
            "subscription": subscription_details,
            "metrics": {
                "total_users": user_count,
                "admin_count": admin_count,
                "is_active": bool(hospital.is_active),
            },
        }
    
    async def update_hospital(self, hospital_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update hospital information"""
        # Get hospital
        result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        hospital = result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Check if registration number is being changed and ensure uniqueness
        if "registration_number" in update_data and update_data["registration_number"] != hospital.registration_number:
            existing_hospital = await self.db.execute(
                select(Hospital).where(
                    and_(
                        Hospital.registration_number == update_data["registration_number"],
                        Hospital.id != hospital_id
                    )
                )
            )
            if existing_hospital.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "REGISTRATION_NUMBER_EXISTS", "message": "Hospital with this registration number already exists"}
                )
        
        # Update fields
        for field, value in update_data.items():
            if hasattr(hospital, field) and value is not None:
                setattr(hospital, field, value)
        
        hospital.updated_at = datetime.utcnow()
        await self.db.commit()
        
        # TODO: Add audit log entry
        
        return {
            "id": str(hospital.id),
            "message": "Hospital updated successfully"
        }
    
    async def update_hospital_status(self, hospital_id: uuid.UUID, new_status: str) -> Dict[str, Any]:
        """Update hospital operational status"""
        # Get hospital
        result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        hospital = result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Validate status
        if new_status not in [HospitalStatus.ACTIVE, HospitalStatus.SUSPENDED, HospitalStatus.INACTIVE]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_STATUS", "message": "Invalid status value"}
            )
        
        # Update status
        old_status = hospital.status
        hospital.status = new_status
        hospital.is_active = new_status == HospitalStatus.ACTIVE
        hospital.updated_at = datetime.utcnow()

        # Tenant user access: align with hospital operational status
        users_query = (
            select(User)
            .options(selectinload(User.roles))
            .where(User.hospital_id == hospital_id)
        )
        users_result = await self.db.execute(users_query)
        users = users_result.scalars().all()

        if new_status in (HospitalStatus.SUSPENDED, HospitalStatus.INACTIVE):
            for user in users:
                user_roles = [role.name for role in user.roles]
                if UserRole.SUPER_ADMIN not in user_roles:
                    user.status = UserStatus.BLOCKED
        elif (
            new_status == HospitalStatus.ACTIVE
            and old_status in (HospitalStatus.SUSPENDED, HospitalStatus.INACTIVE)
        ):
            # Reactivating a shut-down hospital: restore tenant users that were blocked with it
            for user in users:
                user_roles = [role.name for role in user.roles]
                if UserRole.SUPER_ADMIN not in user_roles and user.status == UserStatus.BLOCKED:
                    user.status = UserStatus.ACTIVE

        await self.db.commit()
        
        # TODO: Send notification emails to hospital admins
        # TODO: Add audit log entry
        
        return {
            "hospital_id": str(hospital.id),
            "old_status": old_status,
            "new_status": new_status,
            "message": f"Hospital status updated to {new_status}"
        }
    
    # ============================================================================
    # HOSPITAL ADMINISTRATOR MANAGEMENT
    # ============================================================================
    
    async def get_hospital_admins(self, hospital_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Get list of hospital administrators"""
        # Verify hospital exists
        hospital_result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        if not hospital_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Get hospital admins
        query = select(User).options(
            selectinload(User.roles)
        ).where(
            and_(
                User.hospital_id == hospital_id,
                User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN.value),
            )
        ).order_by(User.created_at.desc())
        
        result = await self.db.execute(query)
        admins = result.scalars().all()
        
        admin_list = []
        for admin in admins:
            admin_list.append({
                "id": str(admin.id),
                "email": admin.email,
                "first_name": admin.first_name,
                "last_name": admin.last_name,
                "phone": admin.phone,
                "status": admin.status,
                "email_verified": admin.email_verified,
                "last_login": admin.last_login.isoformat() if admin.last_login else None,
                "created_at": admin.created_at.isoformat()
            })
        
        return admin_list
    
    async def update_admin_status(self, admin_id: uuid.UUID, new_status: str) -> Dict[str, Any]:
        """Update hospital administrator status"""
        # Get admin user
        query = select(User).options(selectinload(User.roles)).where(User.id == admin_id)
        result = await self.db.execute(query)
        admin = result.scalar_one_or_none()
        
        if not admin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ADMIN_NOT_FOUND", "message": "Hospital administrator not found"}
            )
        
        # Verify user is a hospital admin
        user_roles = [role.name for role in admin.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_HOSPITAL_ADMIN", "message": "User is not a hospital administrator"}
            )
        
        # Validate status
        if new_status not in [UserStatus.ACTIVE, UserStatus.BLOCKED, UserStatus.PENDING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_STATUS", "message": "Invalid status value"}
            )
        
        # Update status
        old_status = admin.status
        admin.status = new_status
        admin.updated_at = datetime.utcnow()
        
        await self.db.commit()
        
        # TODO: Send notification email on status change
        # TODO: Add audit log entry
        
        return {
            "admin_id": str(admin.id),
            "old_status": old_status,
            "new_status": new_status,
            "message": f"Administrator status updated to {new_status}"
        }

    # ============================================================================
    # SUBSCRIPTION PLAN MANAGEMENT
    # ============================================================================
    
    async def create_subscription_plan(self, plan_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new subscription plan"""
        from app.models.tenant import SubscriptionPlanModel
        
        # Check if plan with same name already exists
        existing_plan = await self.db.execute(
            select(SubscriptionPlanModel).where(SubscriptionPlanModel.name == plan_data['name'])
        )
        if existing_plan.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PLAN_EXISTS", "message": "Subscription plan with this name already exists"}
            )
        
        # Create subscription plan
        plan = SubscriptionPlanModel(
            id=uuid.uuid4(),
            name=plan_data['name'],
            display_name=plan_data['display_name'],
            description=plan_data.get('description'),
            monthly_price=plan_data['monthly_price'],
            yearly_price=plan_data['yearly_price'],
            max_doctors=plan_data['max_doctors'],
            max_patients=plan_data['max_patients'],
            max_appointments_per_month=plan_data['max_appointments_per_month'],
            max_storage_gb=plan_data['max_storage_gb'],
            features=plan_data.get('features', {})
        )
        
        self.db.add(plan)
        await self.db.commit()
        
        return {
            "plan_id": str(plan.id),
            "name": plan.name,
            "display_name": plan.display_name,
            "message": "Subscription plan created successfully"
        }
    
    async def get_subscription_plans(self) -> List[Dict[str, Any]]:
        """Get all subscription plans"""
        from app.models.tenant import SubscriptionPlanModel
        
        result = await self.db.execute(
            select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.created_at.desc())
        )
        plans = result.scalars().all()
        
        plan_list = []
        for plan in plans:
            plan_list.append({
                "id": str(plan.id),
                "name": plan.name,
                "display_name": plan.display_name,
                "description": plan.description,
                "monthly_price": float(plan.monthly_price),
                "yearly_price": float(plan.yearly_price),
                "max_doctors": plan.max_doctors,
                "max_patients": plan.max_patients,
                "max_appointments_per_month": plan.max_appointments_per_month,
                "max_storage_gb": plan.max_storage_gb,
                "features": plan.features,
                "created_at": plan.created_at.isoformat(),
                "updated_at": plan.updated_at.isoformat()
            })
        
        return plan_list
    
    async def update_subscription_plan(self, plan_id: uuid.UUID, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing subscription plan"""
        from app.models.tenant import SubscriptionPlanModel
        
        # Get plan
        result = await self.db.execute(select(SubscriptionPlanModel).where(SubscriptionPlanModel.id == plan_id))
        plan = result.scalar_one_or_none()
        
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLAN_NOT_FOUND", "message": "Subscription plan not found"}
            )
        
        # Update fields
        for field, value in update_data.items():
            if hasattr(plan, field) and value is not None:
                setattr(plan, field, value)
        
        plan.updated_at = datetime.utcnow()
        await self.db.commit()
        
        return {
            "plan_id": str(plan.id),
            "message": "Subscription plan updated successfully"
        }
    
    async def delete_subscription_plan(self, plan_id: uuid.UUID) -> Dict[str, Any]:
        """Delete a subscription plan"""
        from app.models.tenant import SubscriptionPlanModel, HospitalSubscription
        
        # Get plan
        result = await self.db.execute(select(SubscriptionPlanModel).where(SubscriptionPlanModel.id == plan_id))
        plan = result.scalar_one_or_none()
        
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLAN_NOT_FOUND", "message": "Subscription plan not found"}
            )
        
        # Check if plan has active subscribers
        subscribers_result = await self.db.execute(
            select(func.count(HospitalSubscription.id)).where(HospitalSubscription.plan_id == plan_id)
        )
        subscriber_count = subscribers_result.scalar()
        
        if subscriber_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PLAN_HAS_SUBSCRIBERS", "message": f"Cannot delete plan with {subscriber_count} active subscribers"}
            )
        
        # Delete plan
        await self.db.delete(plan)
        await self.db.commit()
        
        return {
            "plan_id": str(plan.id),
            "message": "Subscription plan deleted successfully"
        }
    
    async def assign_subscription_plan_by_names(
        self, 
        hospital_name: str, 
        plan_name: str,
        assignment_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assign a subscription plan to a hospital using names instead of IDs"""
        from app.models.tenant import SubscriptionPlanModel, HospitalSubscription
        
        # Find hospital by name
        hospital_result = await self.db.execute(
            select(Hospital).where(Hospital.name == hospital_name)
        )
        hospital = hospital_result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": f"Hospital '{hospital_name}' not found"}
            )
        
        # Find plan by name
        plan_result = await self.db.execute(
            select(SubscriptionPlanModel).where(SubscriptionPlanModel.name == plan_name)
        )
        plan = plan_result.scalar_one_or_none()
        
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLAN_NOT_FOUND", "message": f"Subscription plan '{plan_name}' not found"}
            )
        
        # Use the existing logic with the found IDs
        return await self.assign_subscription_plan(hospital.id, plan.id, assignment_data)

    async def assign_subscription_plan(
        self, 
        hospital_id: uuid.UUID, 
        plan_id: uuid.UUID,
        assignment_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assign a subscription plan to a hospital"""
        from app.models.tenant import SubscriptionPlanModel, HospitalSubscription
        from datetime import timezone as _tz
        
        # Verify hospital exists
        hospital = await self._get_hospital_by_id(hospital_id)
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Verify plan exists
        plan_result = await self.db.execute(select(SubscriptionPlanModel).where(SubscriptionPlanModel.id == plan_id))
        plan = plan_result.scalar_one_or_none()
        
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PLAN_NOT_FOUND", "message": "Subscription plan not found"}
            )
        
        # Check if hospital already has a subscription
        existing_subscription = await self.db.execute(
            select(HospitalSubscription).where(HospitalSubscription.hospital_id == hospital_id)
        )
        current_subscription = existing_subscription.scalar_one_or_none()
        
        # Calculate dates with proper format handling
        start_date = parse_date_string(assignment_data.get('start_date')) or datetime.utcnow()
        if getattr(start_date, "tzinfo", None) is None:
            start_date = start_date.replace(tzinfo=_tz.utc)
        else:
            start_date = start_date.astimezone(_tz.utc)
        if assignment_data.get('end_date'):
            end_date = parse_date_string(assignment_data['end_date'])
        else:
            # Default to 1 year from start date
            end_date = start_date.replace(year=start_date.year + 1)
        if getattr(end_date, "tzinfo", None) is None:
            end_date = end_date.replace(tzinfo=_tz.utc)
        else:
            end_date = end_date.astimezone(_tz.utc)
        
        if current_subscription:
            # Update existing subscription
            current_subscription.plan_id = plan_id
            current_subscription.start_date = start_date
            current_subscription.end_date = end_date
            current_subscription.is_trial = assignment_data.get('is_trial', False)
            current_subscription.auto_renew = assignment_data.get('auto_renew', True)
            current_subscription.status = SubscriptionStatus.ACTIVE
            current_subscription.updated_at = datetime.utcnow()
            
            message = "Hospital subscription updated successfully"
        else:
            # Create new subscription
            subscription = HospitalSubscription(
                id=uuid.uuid4(),
                hospital_id=hospital_id,
                plan_id=plan_id,
                status=SubscriptionStatus.ACTIVE,
                start_date=start_date,
                end_date=end_date,
                is_trial=assignment_data.get('is_trial', False),
                auto_renew=assignment_data.get('auto_renew', True),
                current_usage={}
            )
            
            self.db.add(subscription)
            message = "Hospital subscription created successfully"

        # If the hospital is active, unblock hospital admins/staff that were blocked
        # due to hospital deactivation so they can login again (subscription gating is enforced at login).
        if hospital.is_active and hospital.status == HospitalStatus.ACTIVE:
            users_q = (
                select(User)
                .options(selectinload(User.roles))
                .where(User.hospital_id == hospital_id)
            )
            users_result = await self.db.execute(users_q)
            users = users_result.scalars().all()

            allowed_roles = {
                UserRole.HOSPITAL_ADMIN,
                UserRole.DOCTOR,
                UserRole.NURSE,
                UserRole.RECEPTIONIST,
                UserRole.PHARMACIST,
                UserRole.LAB_TECH,
            }

            for u in users:
                if u.status != UserStatus.BLOCKED:
                    continue
                role_names = {r.name for r in (u.roles or [])}
                if role_names.intersection(allowed_roles):
                    u.status = UserStatus.ACTIVE

        await self.db.commit()
        
        return {
            "hospital_name": hospital.name,
            "plan_name": plan.name,
            "message": message
        }
    
    async def get_hospital_subscription(self, hospital_id: uuid.UUID) -> Dict[str, Any]:
        """Get hospital subscription details"""
        from app.models.tenant import HospitalSubscription, SubscriptionPlanModel
        
        # Verify hospital exists
        hospital = await self._get_hospital_by_id(hospital_id)
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        
        # Get subscription with plan details
        query = select(HospitalSubscription).options(
            selectinload(HospitalSubscription.plan)
        ).where(HospitalSubscription.hospital_id == hospital_id)
        
        result = await self.db.execute(query)
        subscription = result.scalar_one_or_none()
        
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SUBSCRIPTION_NOT_FOUND", "message": "Hospital subscription not found"}
            )
        
        # Get usage metrics (placeholder - would be calculated from actual usage)
        current_usage = subscription.current_usage or {}
        
        return {
            "hospital_name": hospital.name,
            "plan": {
                "name": subscription.plan.name,
                "display_name": subscription.plan.display_name,
                "monthly_price": float(subscription.plan.monthly_price),
                "yearly_price": float(subscription.plan.yearly_price),
                "features": subscription.plan.features
            },
            "status": subscription.status,
            "start_date": subscription.start_date.isoformat(),
            "end_date": subscription.end_date.isoformat(),
            "is_trial": subscription.is_trial,
            "trial_end_date": subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
            "auto_renew": subscription.auto_renew,
            "current_usage": current_usage,
            "limits": {
                "max_doctors": subscription.plan.max_doctors,
                "max_patients": subscription.plan.max_patients,
                "max_appointments_per_month": subscription.plan.max_appointments_per_month,
                "max_storage_gb": subscription.plan.max_storage_gb
            },
            "created_at": subscription.created_at.isoformat(),
            "updated_at": subscription.updated_at.isoformat()
        }
    
    async def get_hospital_subscription_by_name(self, hospital_name: str) -> Dict[str, Any]:
        """Get hospital subscription details using hospital name"""
        from app.models.tenant import HospitalSubscription, SubscriptionPlanModel
        
        # Find hospital by name
        hospital_result = await self.db.execute(
            select(Hospital).where(Hospital.name == hospital_name)
        )
        hospital = hospital_result.scalar_one_or_none()
        
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": f"Hospital '{hospital_name}' not found"}
            )
        
        # Use the existing method with the found hospital ID
        return await self.get_hospital_subscription(hospital.id)
    
    # ============================================================================
    # HELPER METHODS
    # ============================================================================
    
    async def _get_hospital_by_id(self, hospital_id: uuid.UUID) -> Optional[Hospital]:
        """Get hospital by ID"""
        result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        return result.scalar_one_or_none()
    
    async def _verify_super_admin_access(self, user: User) -> None:
        """Verify user has Super Admin access"""
        user_roles = [role.name for role in user.roles]
        if UserRole.SUPER_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "INSUFFICIENT_PERMISSIONS", "message": "Super Admin access required"}
            )
    
    async def _log_admin_action(
        self, 
        user_id: uuid.UUID, 
        action: str, 
        resource_type: str, 
        resource_id: Optional[uuid.UUID] = None,
        description: str = "",
        old_values: Optional[Dict] = None,
        new_values: Optional[Dict] = None
    ) -> None:
        """Log administrative action for audit purposes"""
        audit_log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            description=description,
            old_values=old_values,
            new_values=new_values,
            is_sensitive=True  # All Super Admin actions are sensitive
        )
        
        self.db.add(audit_log)
        # Note: Commit will be handled by the calling method

    # ============================================================================
    # PLATFORM ANALYTICS & DASHBOARD
    # ============================================================================

    async def get_platform_analytics(self) -> Dict[str, Any]:
        """
        Super Admin dashboard overview KPI cards:
        - Total appointments, beds, billing (successful payments), doctors (platform-wide).
        Subscription breakdown retained. No hospital-count or admin-count KPIs here.
        """
        from app.models.billing import BillingPayment
        from app.models.hospital import Bed
        from app.models.patient import Appointment
        from app.models.doctor import DoctorProfile

        total_appointments = (
            await self.db.execute(select(func.count(Appointment.id)))
        ).scalar() or 0

        total_beds = (
            await self.db.execute(select(func.count(Bed.id)))
        ).scalar() or 0

        total_doctors = (
            await self.db.execute(select(func.count(DoctorProfile.id)))
        ).scalar() or 0

        rev_query = select(
            func.coalesce(func.sum(BillingPayment.amount), 0).label("total")
        ).where(BillingPayment.status == "SUCCESS")
        rev_result = await self.db.execute(rev_query)
        total_billing = float(rev_result.scalar() or 0)

        # Subscriptions by plan
        sub_query = (
            select(SubscriptionPlanModel.name, func.count(HospitalSubscription.id))
            .select_from(SubscriptionPlanModel)
            .join(HospitalSubscription, HospitalSubscription.plan_id == SubscriptionPlanModel.id)
            .where(HospitalSubscription.status == SubscriptionStatus.ACTIVE)
            .group_by(SubscriptionPlanModel.name)
        )
        sub_result = await self.db.execute(sub_query)
        subscriptions_by_plan = {row[0]: row[1] for row in sub_result.all()}

        return {
            "overview": {
                "total_appointments": int(total_appointments),
                "total_beds": int(total_beds),
                "total_billing": total_billing,
                "total_doctors": int(total_doctors),
            },
            "subscriptions": {
                "by_plan": subscriptions_by_plan,
                "active_count": sum(subscriptions_by_plan.values()),
            },
            "revenue": {
                "total": total_billing,
            },
        }

    async def get_dashboard_overview_cards(
        self,
        period_days: int = 30,
        trend_months: int = 6,
    ) -> Dict[str, Any]:
        """
        Super Admin home dashboard — three KPI cards:
        operational hospitals count, paid active subscriptions, platform revenue (successful payments).
        Includes period-over-period growth and per-month series for sparkline/bar charts.
        """
        from datetime import timezone as _tz
        from dateutil.relativedelta import relativedelta
        from app.models.billing import BillingPayment

        now = datetime.now(_tz.utc)
        period_days = max(1, min(365, int(period_days)))
        trend_months = max(1, min(24, int(trend_months)))

        cur_start = now - timedelta(days=period_days)
        prev_start = now - timedelta(days=2 * period_days)

        operational = and_(
            Hospital.status == HospitalStatus.ACTIVE.value,
            Hospital.is_active.is_(True),
        )

        total_hospitals = int(
            (await self.db.execute(select(func.count(Hospital.id)).where(operational))).scalar() or 0
        )

        new_h_cur = int(
            (
                await self.db.execute(
                    select(func.count(Hospital.id)).where(Hospital.created_at >= cur_start)
                )
            ).scalar()
            or 0
        )
        new_h_prev = int(
            (
                await self.db.execute(
                    select(func.count(Hospital.id)).where(
                        Hospital.created_at >= prev_start,
                        Hospital.created_at < cur_start,
                    )
                )
            ).scalar()
            or 0
        )

        paid_plan_filter = SubscriptionPlanModel.name != SubscriptionPlan.FREE.value

        active_paid_plans = int(
            (
                await self.db.execute(
                    select(func.count(HospitalSubscription.id))
                    .join(SubscriptionPlanModel, SubscriptionPlanModel.id == HospitalSubscription.plan_id)
                    .where(
                        HospitalSubscription.status == SubscriptionStatus.ACTIVE.value,
                        paid_plan_filter,
                    )
                )
            ).scalar()
            or 0
        )

        new_sub_cur = int(
            (
                await self.db.execute(
                    select(func.count(HospitalSubscription.id))
                    .join(SubscriptionPlanModel, SubscriptionPlanModel.id == HospitalSubscription.plan_id)
                    .where(
                        HospitalSubscription.created_at >= cur_start,
                        paid_plan_filter,
                    )
                )
            ).scalar()
            or 0
        )
        new_sub_prev = int(
            (
                await self.db.execute(
                    select(func.count(HospitalSubscription.id))
                    .join(SubscriptionPlanModel, SubscriptionPlanModel.id == HospitalSubscription.plan_id)
                    .where(
                        HospitalSubscription.created_at >= prev_start,
                        HospitalSubscription.created_at < cur_start,
                        paid_plan_filter,
                    )
                )
            ).scalar()
            or 0
        )

        platform_revenue_total = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(BillingPayment.amount), 0)).where(
                        BillingPayment.status == "SUCCESS"
                    )
                )
            ).scalar()
            or 0
        )

        rev_cur = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(BillingPayment.amount), 0)).where(
                        BillingPayment.status == "SUCCESS",
                        BillingPayment.paid_at >= cur_start,
                        BillingPayment.paid_at.isnot(None),
                    )
                )
            ).scalar()
            or 0
        )
        rev_prev = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(BillingPayment.amount), 0)).where(
                        BillingPayment.status == "SUCCESS",
                        BillingPayment.paid_at >= prev_start,
                        BillingPayment.paid_at < cur_start,
                        BillingPayment.paid_at.isnot(None),
                    )
                )
            ).scalar()
            or 0
        )

        def _growth(curr: float, prev: float) -> float:
            if prev > 0:
                return round((curr - prev) / prev * 100, 2)
            if curr > 0:
                return 100.0
            return 0.0

        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(
            months=trend_months - 1
        )

        hospitals_trend: List[Dict[str, Any]] = []
        subscriptions_trend: List[Dict[str, Any]] = []
        revenue_trend: List[Dict[str, Any]] = []

        for i in range(trend_months):
            ms = month_start + relativedelta(months=i)
            me = ms + relativedelta(months=1)
            period_label = ms.strftime("%Y-%m")

            hc = int(
                (
                    await self.db.execute(
                        select(func.count(Hospital.id)).where(
                            Hospital.created_at >= ms,
                            Hospital.created_at < me,
                        )
                    )
                ).scalar()
                or 0
            )
            hospitals_trend.append({"period": period_label, "value": hc})

            sc = int(
                (
                    await self.db.execute(
                        select(func.count(HospitalSubscription.id))
                        .join(SubscriptionPlanModel, SubscriptionPlanModel.id == HospitalSubscription.plan_id)
                        .where(
                            HospitalSubscription.created_at >= ms,
                            HospitalSubscription.created_at < me,
                            paid_plan_filter,
                        )
                    )
                ).scalar()
                or 0
            )
            subscriptions_trend.append({"period": period_label, "value": sc})

            rv = float(
                (
                    await self.db.execute(
                        select(func.coalesce(func.sum(BillingPayment.amount), 0)).where(
                            BillingPayment.status == "SUCCESS",
                            BillingPayment.paid_at >= ms,
                            BillingPayment.paid_at < me,
                        )
                    )
                ).scalar()
                or 0
            )
            revenue_trend.append({"period": period_label, "value": round(rv, 2)})

        return {
            "period_days": period_days,
            "trend_months": trend_months,
            "total_hospitals": {
                "title": "TOTAL HOSPITALS",
                "value": total_hospitals,
                "subtitle": "Currently operational",
                "growth_percent": _growth(float(new_h_cur), float(new_h_prev)),
                "growth_basis": (
                    f"New hospital registrations in the last {period_days} days vs the previous {period_days} days."
                ),
                "trend": hospitals_trend,
            },
            "active_plans": {
                "title": "ACTIVE PLANS",
                "value": active_paid_plans,
                "subtitle": "Paid subscriptions",
                "growth_percent": _growth(float(new_sub_cur), float(new_sub_prev)),
                "growth_basis": (
                    f"New paid (non-FREE) subscriptions created in the last {period_days} days vs the previous "
                    f"{period_days} days."
                ),
                "trend": subscriptions_trend,
            },
            "platform_revenue": {
                "title": "PLATFORM REVENUE",
                "value": round(platform_revenue_total, 2),
                "currency": "INR",
                "subtitle": "All hospitals combined",
                "growth_percent": _growth(rev_cur, rev_prev),
                "growth_basis": (
                    f"Successful billing payments with paid_at in the last {period_days} days vs the previous "
                    f"{period_days} days."
                ),
                "trend": revenue_trend,
            },
        }

    # ============================================================================
    # SUPER ADMIN - ANALYTICS REPORTS (Subscription / Financial / Performance)
    # ============================================================================

    async def get_subscription_analytics(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        plan_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Subscription lifecycle analytics for dashboard.
        Uses existing HospitalSubscription + Plan models.
        """
        from datetime import date as _date, datetime as _dt, timezone as _tz
        from sqlalchemy import case

        def _to_utc_datetime(v: Any) -> Optional[_dt]:
            if v is None:
                return None
            if isinstance(v, _dt):
                return v if v.tzinfo else v.replace(tzinfo=_tz.utc)
            if isinstance(v, _date):
                return _dt(v.year, v.month, v.day, tzinfo=_tz.utc)
            if isinstance(v, str):
                try:
                    parsed = parse_date_string(v)
                    if parsed:
                        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_tz.utc)
                except Exception:
                    return None
            return None

        def _to_iso_date(v: Any) -> Optional[str]:
            if v is None:
                return None
            if isinstance(v, _dt):
                return v.date().isoformat()
            if isinstance(v, _date):
                return v.isoformat()
            if isinstance(v, str):
                try:
                    parsed = parse_date_string(v)
                    if parsed:
                        return parsed.date().isoformat()
                except Exception:
                    return v[:10] if len(v) >= 10 else v
            return None

        def _status_value(v: Any) -> str:
            return getattr(v, "value", str(v or "")).upper()

        now = _dt.now(_tz.utc)
        if date_from and getattr(date_from, "tzinfo", None) is None:
            date_from = date_from.replace(tzinfo=_tz.utc)
        if date_to and getattr(date_to, "tzinfo", None) is None:
            date_to = date_to.replace(tzinfo=_tz.utc)

        # Core rows for table + summary
        q = (
            select(Hospital, HospitalSubscription, SubscriptionPlanModel)
            .join(HospitalSubscription, HospitalSubscription.hospital_id == Hospital.id)
            .join(SubscriptionPlanModel, SubscriptionPlanModel.id == HospitalSubscription.plan_id)
        )
        if plan_name:
            q = q.where(SubscriptionPlanModel.name == plan_name)
        q = q.order_by(Hospital.name.asc())
        r = await self.db.execute(q)
        rows = r.all()

        subscriptions: List[Dict[str, Any]] = []
        active = expired = cancelled = suspended = 0

        for hospital, sub, plan in rows:
            # Determine expiry in a timezone-safe way
            end_dt = _to_utc_datetime(getattr(sub, "end_date", None))
            if end_dt and end_dt < now:
                effective_status = SubscriptionStatus.EXPIRED
            else:
                effective_status = getattr(sub, "status", None)
            effective_status_value = _status_value(effective_status)
            if status and effective_status_value != str(status).strip().upper():
                continue

            if effective_status_value == SubscriptionStatus.ACTIVE.value:
                active += 1
            elif effective_status_value == SubscriptionStatus.EXPIRED.value:
                expired += 1
            elif effective_status_value == SubscriptionStatus.CANCELLED.value:
                cancelled += 1
            elif effective_status_value == SubscriptionStatus.SUSPENDED.value:
                suspended += 1

            # Amount paid: if you later add invoicing, wire it here.
            amount_paid = 0
            billing_cycle = "yearly"
            if plan and float(plan.monthly_price or 0) > 0 and float(plan.yearly_price or 0) <= 0:
                billing_cycle = "monthly"

            subscriptions.append(
                {
                    "hospitalName": hospital.name,
                    "planType": plan.display_name if plan else None,
                    "subscriptionStartDate": _to_iso_date(getattr(sub, "start_date", None)),
                    "subscriptionEndDate": _to_iso_date(getattr(sub, "end_date", None)),
                    "status": effective_status_value.lower(),
                    "billingCycle": billing_cycle,
                    "amountPaid": amount_paid,
                    "renewalDate": _to_iso_date(getattr(sub, "end_date", None)),
                    "autoRenewal": bool(sub.auto_renew),
                }
            )

        total_hospitals = len({row[0].id for row in rows})

        # Monthly growth chart (based on subscription created_at)
        # date_trunc is Postgres; works on Render. For SQLite dev, it may differ.
        growth_rows = []
        try:
            gq = (
                select(
                    func.date_trunc("month", HospitalSubscription.created_at).label("m"),
                    func.count(HospitalSubscription.id).label("new_subscriptions"),
                )
                .where(
                    *[
                        cond
                        for cond in [
                            (HospitalSubscription.created_at >= date_from) if date_from else None,
                            (HospitalSubscription.created_at <= date_to) if date_to else None,
                        ]
                        if cond is not None
                    ]
                )
                .group_by(func.date_trunc("month", HospitalSubscription.created_at))
                .order_by(func.date_trunc("month", HospitalSubscription.created_at))
            )
            gr = await self.db.execute(gq)
            for m, new_subscriptions in gr.all():
                growth_rows.append(
                    {
                        "month": m.strftime("%b") if m else None,
                        "newSubscriptions": int(new_subscriptions or 0),
                        "renewals": 0,
                        "churned": 0,
                        "netGrowth": int(new_subscriptions or 0),
                    }
                )
        except Exception:
            # If a query fails, PostgreSQL marks the transaction as aborted.
            # Roll back so subsequent best-effort analytics queries can continue.
            await self.db.rollback()
            growth_rows = []

        # Plan distribution
        plans_rows = []
        pq = (
            select(
                SubscriptionPlanModel.display_name,
                func.count(HospitalSubscription.id).label("hospitals"),
            )
            .join(HospitalSubscription, HospitalSubscription.plan_id == SubscriptionPlanModel.id)
            .group_by(SubscriptionPlanModel.display_name)
            .order_by(func.count(HospitalSubscription.id).desc())
        )
        pr = await self.db.execute(pq)
        for display_name, hospitals_count in pr.all():
            plans_rows.append(
                {
                    "plan": display_name,
                    "hospitals": int(hospitals_count or 0),
                    "revenue": 0,
                }
            )

        # Churn analysis chart (counts expired/cancelled by month based on end_date)
        churn_rows = []
        try:
            cq = (
                select(
                    func.date_trunc("month", HospitalSubscription.end_date).label("m"),
                    func.count(
                        case(
                            (HospitalSubscription.status == SubscriptionStatus.CANCELLED, 1),
                            else_=None,
                        )
                    ).label("cancellations"),
                    func.count(
                        case(
                            (HospitalSubscription.end_date < now, 1),
                            else_=None,
                        )
                    ).label("expired"),
                )
                .where(HospitalSubscription.end_date.is_not(None))
                .group_by(func.date_trunc("month", HospitalSubscription.end_date))
                .order_by(func.date_trunc("month", HospitalSubscription.end_date))
            )
            cr = await self.db.execute(cq)
            for m, cancellations, expired_cnt in cr.all():
                cancellations = int(cancellations or 0)
                expired_cnt = int(expired_cnt or 0)
                base = max(total_hospitals, 1)
                churn_rate_m = round(((cancellations + expired_cnt) / base) * 100, 2)
                churn_rows.append(
                    {
                        "month": m.strftime("%b") if m else None,
                        "churnRate": churn_rate_m,
                        "cancellations": cancellations,
                        "expired": expired_cnt,
                    }
                )
        except Exception:
            # Reset aborted transaction state before continuing.
            await self.db.rollback()
            churn_rows = []

        # Revenue contribution chart: placeholders (no subscription payments table yet)
        revenue_contribution_rows = [
            {
                "month": x.get("month"),
                "mrr": 0,
                "arr": 0,
                "renewalRevenue": 0,
                "newRevenue": 0,
            }
            for x in growth_rows
        ]

        churn_rate = round((cancelled / total_hospitals * 100), 2) if total_hospitals else 0.0
        retention_rate = round(100 - churn_rate, 2) if total_hospitals else 0.0

        return {
            "summary": {
                "subscription": {
                    "totalHospitals": total_hospitals,
                    "activeSubscriptions": active,
                    "expiredSubscriptions": expired,
                    "cancelledSubscriptions": cancelled,
                    "suspendedSubscriptions": suspended,
                    "churnRate": churn_rate,
                    "retentionRate": retention_rate,
                    "newSubscriptions": sum(x["newSubscriptions"] for x in growth_rows) if growth_rows else 0,
                    "renewals": 0,
                    "upgrades": 0,
                    "downgrades": 0,
                }
            },
            "subscriptions": subscriptions,
            "growth": growth_rows,
            "plans": plans_rows,
            "churn": churn_rows,
            "revenueContribution": revenue_contribution_rows,
        }

    async def get_financial_analytics(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        hospital_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        """
        Financial analytics for dashboard.
        Uses BillingPayment + Bill tables (existing).
        """
        from datetime import datetime as _dt, timezone as _tz
        from app.models.billing import BillingPayment, Bill

        if date_from and getattr(date_from, "tzinfo", None) is None:
            date_from = date_from.replace(tzinfo=_tz.utc)
        if date_to and getattr(date_to, "tzinfo", None) is None:
            date_to = date_to.replace(tzinfo=_tz.utc)

        # Transactions table: recent payments
        tq = (
            select(BillingPayment, Bill)
            .join(Bill, Bill.id == BillingPayment.bill_id)
            .where(
                *[
                    cond
                    for cond in [
                        (BillingPayment.hospital_id == hospital_id) if hospital_id else None,
                        (BillingPayment.paid_at >= date_from) if date_from else None,
                        (BillingPayment.paid_at <= date_to) if date_to else None,
                    ]
                    if cond is not None
                ]
            )
            .order_by((BillingPayment.paid_at.is_(None)).asc(), BillingPayment.paid_at.desc().nullslast())
            .limit(200)
        )
        tr = await self.db.execute(tq)
        transactions = []
        for p, bill in tr.all():
            transactions.append(
                {
                    "hospitalName": None,  # payments are hospital-scoped; UI may not need this row-level
                    "planType": None,
                    "billingCycle": None,
                    "invoiceId": bill.bill_number if bill else None,
                    "invoiceDate": bill.created_at.date().isoformat() if bill and bill.created_at else None,
                    "dueDate": None,
                    "paymentDate": p.paid_at.date().isoformat() if p.paid_at else None,
                    "amount": float(p.amount or 0),
                    "tax": float(getattr(bill, "tax_amount", 0) or 0) if bill else 0,
                    "totalAmount": float(getattr(bill, "total_amount", 0) or 0) if bill else float(p.amount or 0),
                    "status": str(p.status).lower(),
                    "paymentMethod": p.method,
                    "transactionId": p.gateway_transaction_id or p.payment_ref,
                }
            )

        # Summary metrics
        rev_q = (
            select(func.coalesce(func.sum(BillingPayment.amount), 0))
            .where(
                BillingPayment.status == "SUCCESS",
                *[
                    cond
                    for cond in [
                        (BillingPayment.hospital_id == hospital_id) if hospital_id else None,
                        (BillingPayment.paid_at >= date_from) if date_from else None,
                        (BillingPayment.paid_at <= date_to) if date_to else None,
                    ]
                    if cond is not None
                ],
            )
        )
        total_revenue = float((await self.db.execute(rev_q)).scalar() or 0)

        # MRR/ARR (simple: revenue / months elapsed isn't meaningful; keep derived as placeholders)
        mrr = round(total_revenue / 12, 2) if total_revenue else 0.0
        arr = total_revenue

        paid_count_q = select(func.count(BillingPayment.id)).where(BillingPayment.status == "SUCCESS")
        paid_invoices = int((await self.db.execute(paid_count_q)).scalar() or 0)

        pending_count_q = select(func.count(BillingPayment.id)).where(BillingPayment.status == "INITIATED")
        pending_invoices = int((await self.db.execute(pending_count_q)).scalar() or 0)

        summary = {
            "financial": {
                "totalRevenue": total_revenue,
                "monthlyRecurringRevenue": mrr,
                "annualRecurringRevenue": arr,
                "averageRevenuePerUser": 0,
                "collectionRate": 0,
                "outstandingAmount": 0,
                "overdueInvoices": 0,
                "paidInvoices": paid_invoices,
                "pendingInvoices": pending_invoices,
                "profitMargin": 0,
            }
        }

        # Revenue trends chart (month buckets)
        revenue_trends = []
        try:
            rq = (
                select(
                    func.date_trunc("month", BillingPayment.paid_at).label("m"),
                    func.coalesce(func.sum(BillingPayment.amount), 0).label("mrr"),
                )
                .where(
                    BillingPayment.status == "SUCCESS",
                    BillingPayment.paid_at.is_not(None),
                    *[
                        cond
                        for cond in [
                            (BillingPayment.hospital_id == hospital_id) if hospital_id else None,
                            (BillingPayment.paid_at >= date_from) if date_from else None,
                            (BillingPayment.paid_at <= date_to) if date_to else None,
                        ]
                        if cond is not None
                    ],
                )
                .group_by(func.date_trunc("month", BillingPayment.paid_at))
                .order_by(func.date_trunc("month", BillingPayment.paid_at))
            )
            rr = await self.db.execute(rq)
            for m, mrr_val in rr.all():
                mrr_val = float(mrr_val or 0)
                revenue_trends.append(
                    {
                        "month": m.strftime("%b") if m else None,
                        "mrr": mrr_val,
                        "arr": mrr_val * 12,
                        "newRevenue": 0,
                        "churnRevenue": 0,
                        "netRevenue": mrr_val,
                    }
                )
        except Exception:
            revenue_trends = []

        # Collections chart (month buckets)
        collections = [
            {
                "month": x["month"],
                "collectedAmount": x["mrr"],
                "pendingAmount": 0,
                "overdueAmount": 0,
                "collectionRate": 0,
            }
            for x in revenue_trends
        ]

        # Payment status breakdown
        payment_status = []
        try:
            psq = (
                select(BillingPayment.status, func.count(BillingPayment.id))
                .group_by(BillingPayment.status)
                .order_by(func.count(BillingPayment.id).desc())
            )
            psr = await self.db.execute(psq)
            for st, cnt in psr.all():
                payment_status.append({"status": str(st).title(), "count": int(cnt or 0)})
        except Exception:
            payment_status = []

        return {
            "summary": summary,
            "transactions": transactions,
            "revenueTrends": revenue_trends,
            "collections": collections,
            "plans": [],
            "paymentStatus": payment_status,
        }

    async def get_performance_analytics(self) -> Dict[str, Any]:
        """
        Platform performance analytics.
        NOTE: True API-level telemetry isn't stored yet; this returns DB-backed high-level counts.
        """
        from app.models.user import AuditLog

        total_requests = 0
        failed_requests = 0
        try:
            # Best-effort proxy: audit logs count (not real HTTP request logs)
            total_requests = int((await self.db.execute(select(func.count(AuditLog.id)))).scalar() or 0)
        except Exception:
            total_requests = 0

        summary = {
            "performance": {
                "platformUptime": None,
                "apiResponseTime": None,
                "peakResponseTime": None,
                "errorRate": None,
                "successRate": None,
                "totalRequests": total_requests,
                "failedRequests": failed_requests,
                "activeSessions": None,
                "serverLoad": None,
                "cpuUsage": None,
                "memoryUsage": None,
            }
        }
        return {
            "summary": summary,
            "logs": [],
            "responseTrends": [],
            "errors": [],
            "resources": [],
        }

    async def delete_hospital(self, hospital_id: uuid.UUID) -> Dict[str, Any]:
        """Soft-delete hospital: INACTIVE + block tenant users. Super Admin auth is the only gate."""
        from sqlalchemy.exc import SQLAlchemyError

        hospital = await self._get_hospital_by_id(hospital_id)
        if not hospital:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "HOSPITAL_NOT_FOUND", "message": "Hospital not found"}
            )
        hospital.status = HospitalStatus.INACTIVE
        hospital.is_active = False
        hospital.updated_at = datetime.utcnow()
        users_result = await self.db.execute(
            select(User).options(selectinload(User.roles)).where(User.hospital_id == hospital_id)
        )
        for user in users_result.scalars().all():
            user_roles = [r.name for r in user.roles]
            if UserRole.SUPER_ADMIN not in user_roles:
                user.status = UserStatus.BLOCKED
        try:
            await self.db.commit()
        except SQLAlchemyError as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "HOSPITAL_DELETE_FAILED",
                    "message": "Could not deactivate hospital. Try again or contact support.",
                },
            ) from e
        return {
            "hospital_id": str(hospital_id),
            "message": "Hospital deactivated successfully",
            "status": HospitalStatus.INACTIVE.value,
        }

    async def reset_admin_password(self, admin_id: uuid.UUID) -> Dict[str, Any]:
        """Reset hospital admin password: generate temp, set hash, return temp password."""
        query = select(User).options(selectinload(User.roles)).where(User.id == admin_id)
        result = await self.db.execute(query)
        admin = result.scalar_one_or_none()
        if not admin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ADMIN_NOT_FOUND", "message": "Administrator not found"}
            )
        user_roles = [r.name for r in admin.roles]
        if UserRole.HOSPITAL_ADMIN not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_HOSPITAL_ADMIN", "message": "User is not a hospital administrator"}
            )
        temp_password = self.security.generate_temp_password(12)
        admin.password_hash = self.security.hash_password(temp_password)
        admin.password_changed_at = datetime.utcnow()
        admin.updated_at = datetime.utcnow()
        await self.db.commit()
        return {"admin_id": str(admin_id), "temp_password": temp_password, "email": admin.email}

    async def get_platform_audit_logs(
        self, skip: int = 0, limit: int = 50, resource_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get platform audit logs (across all hospitals)."""
        conditions = []
        if resource_type:
            conditions.append(AuditLog.resource_type == resource_type)
        count_q = select(func.count(AuditLog.id)).where(and_(*conditions)) if conditions else select(func.count(AuditLog.id))
        total = (await self.db.execute(count_q)).scalar() or 0
        q = select(AuditLog).order_by(desc(AuditLog.created_at)).offset(skip).limit(limit)
        if conditions:
            q = q.where(and_(*conditions))
        r = await self.db.execute(q)
        logs = r.scalars().all()
        log_list = []
        for log in logs:
            log_list.append({
                "id": str(log.id),
                "user_id": str(log.user_id),
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "description": log.description,
                "hospital_id": str(log.hospital_id) if log.hospital_id else None,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })
        return {"logs": log_list, "total": total, "skip": skip, "limit": limit}

    async def create_support_ticket(self, hospital_id: uuid.UUID, raised_by_user_id: uuid.UUID, subject: str, description: str, priority: str = "NORMAL") -> Dict[str, Any]:
        """Create support ticket (can be called by Hospital Admin or Super Admin on their behalf)."""
        from app.models.support import SupportTicket
        ticket = SupportTicket(hospital_id=hospital_id, raised_by_user_id=raised_by_user_id, subject=subject, description=description, status="OPEN", priority=priority)
        self.db.add(ticket)
        await self.db.flush()
        await self.db.commit()
        return {"ticket_id": str(ticket.id), "status": "OPEN", "message": "Ticket created"}

    def _normalize_ticket_status_filter(self, status: Optional[str]) -> Optional[str]:
        """Frontends often send status=all; only real statuses should filter."""
        if not status:
            return None
        s = str(status).strip()
        if not s or s.lower() in ("all", "any", "*"):
            return None
        return s

    async def list_support_tickets(
        self, hospital_id: Optional[uuid.UUID] = None, status: Optional[str] = None, skip: int = 0, limit: int = 50
    ) -> Dict[str, Any]:
        """List support tickets: merged from each hospital's dedicated database when provisioned."""
        from app.models.support import SupportTicket
        from app.database.session import get_tenant_session_factory

        status = self._normalize_ticket_status_filter(status)

        hq = select(Hospital.id, Hospital.tenant_database_name).where(Hospital.tenant_database_name.isnot(None))
        if hospital_id:
            hq = hq.where(Hospital.id == hospital_id)
        hrows = (await self.db.execute(hq)).all()

        if not hrows:
            return await self._list_support_tickets_platform_legacy(
                hospital_id=hospital_id, status=status, skip=skip, limit=limit
            )

        combined: List[Dict[str, Any]] = []
        for hid, tdb in hrows:
            fac = get_tenant_session_factory(tdb)
            async with fac() as s:
                cond = [SupportTicket.hospital_id == hid]
                if status:
                    cond.append(SupportTicket.status == status)
                q = select(SupportTicket).where(and_(*cond)).order_by(desc(SupportTicket.created_at))
                res = await s.execute(q)
                for t in res.scalars().all():
                    combined.append(
                        {
                            "id": str(t.id),
                            "hospital_id": str(t.hospital_id),
                            "subject": t.subject,
                            "status": t.status,
                            "priority": t.priority,
                            "created_at": t.created_at.isoformat() if t.created_at else None,
                        }
                    )

        combined.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        total = len(combined)
        page = combined[skip : skip + limit]
        return {"tickets": page, "total": total, "skip": skip, "limit": limit}

    async def _list_support_tickets_platform_legacy(
        self,
        hospital_id: Optional[uuid.UUID],
        status: Optional[str],
        skip: int,
        limit: int,
    ) -> Dict[str, Any]:
        """Pre–per-tenant DB: tickets lived on the platform database."""
        from app.models.support import SupportTicket

        status = self._normalize_ticket_status_filter(status)
        conditions = []
        if hospital_id:
            conditions.append(SupportTicket.hospital_id == hospital_id)
        if status:
            conditions.append(SupportTicket.status == status)
        count_q = select(func.count(SupportTicket.id))
        if conditions:
            count_q = count_q.where(and_(*conditions))
        total = (await self.db.execute(count_q)).scalar() or 0
        q = select(SupportTicket).order_by(desc(SupportTicket.created_at)).offset(skip).limit(limit)
        if conditions:
            q = q.where(and_(*conditions))
        r = await self.db.execute(q)
        tickets = r.scalars().all()
        return {
            "tickets": [
                {
                    "id": str(t.id),
                    "hospital_id": str(t.hospital_id),
                    "subject": t.subject,
                    "status": t.status,
                    "priority": t.priority,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tickets
            ],
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    async def update_support_ticket_status(
        self,
        ticket_id: uuid.UUID,
        new_status: str,
        resolution_notes: Optional[str] = None,
        assigned_to_user_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        """Update ticket in the hospital's tenant DB, or platform (legacy)."""
        from app.models.support import SupportTicket
        from app.database.session import get_tenant_session_factory

        valid = ["OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED", "CLOSED"]
        if new_status not in valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_STATUS", "message": f"Valid: {valid}"},
            )

        hrows = (
            await self.db.execute(
                select(Hospital.id, Hospital.tenant_database_name).where(Hospital.tenant_database_name.isnot(None))
            )
        ).all()

        for _hid, tdb in hrows:
            fac = get_tenant_session_factory(tdb)
            async with fac() as s:
                r = await s.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
                ticket = r.scalar_one_or_none()
                if not ticket:
                    continue
                ticket.status = new_status
                if resolution_notes is not None:
                    ticket.resolution_notes = resolution_notes
                if assigned_to_user_id is not None:
                    ticket.assigned_to_user_id = assigned_to_user_id
                if new_status in ("RESOLVED", "CLOSED"):
                    ticket.resolved_at = datetime.utcnow()
                ticket.updated_at = datetime.utcnow()
                await s.commit()
                return {
                    "ticket_id": str(ticket_id),
                    "status": new_status,
                    "message": "Ticket updated",
                    "raised_by_user_id": str(ticket.raised_by_user_id),
                }

        r = await self.db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
        ticket = r.scalar_one_or_none()
        if not ticket:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "TICKET_NOT_FOUND", "message": "Support ticket not found"},
            )
        ticket.status = new_status
        if resolution_notes is not None:
            ticket.resolution_notes = resolution_notes
        if assigned_to_user_id is not None:
            ticket.assigned_to_user_id = assigned_to_user_id
        if new_status in ("RESOLVED", "CLOSED"):
            ticket.resolved_at = datetime.utcnow()
        ticket.updated_at = datetime.utcnow()
        await self.db.commit()
        return {
            "ticket_id": str(ticket_id),
            "status": new_status,
            "message": "Ticket updated",
            "raised_by_user_id": str(ticket.raised_by_user_id),
        }

    async def notify_hospital_admins(self, hospital_id: Optional[uuid.UUID], subject: str, message: str, created_by_user_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
        """Send notification to hospital admins. If hospital_id is None, send to all hospital admins (platform-wide)."""
        from app.services.notifications import NotificationService
        query = select(User).options(selectinload(User.roles)).where(
            User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN.value),
        )
        if hospital_id:
            query = query.where(User.hospital_id == hospital_id)
        r = await self.db.execute(query)
        admins = r.scalars().all()
        if not admins:
            return {"sent": 0, "message": "No hospital admins found"}
        sent = 0
        for admin in admins:
            if not admin.email:
                continue
            hid = admin.hospital_id
            if not hid:
                continue
            try:
                svc = NotificationService(self.db, hid)
                key = f"super_admin_notify:{hid}:{admin.id}:{datetime.utcnow().isoformat()}"
                await svc.send(channel="EMAIL", to=admin.email, idempotency_key=key, event_type="ADMIN_NOTIFICATION", raw_message=message, subject=subject, created_by_user_id=created_by_user_id)
                sent += 1
            except Exception as e:
                logger.exception("notify_hospital_admins: enqueue failed for admin %s: %s", admin.id, e)
        await self.db.commit()
        out: Dict[str, Any] = {
            "sent": sent,
            "total_admins": len(admins),
            "message": f"Queued {sent} notification(s)",
        }
        if hospital_id:
            out["hospital_id"] = str(hospital_id)
        return out