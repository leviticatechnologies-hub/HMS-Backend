"""
Service layer for Lab Profile screen.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabProfileConfig
from app.schemas.lab_profile import (
    ChangePasswordResponse,
    ConfigureLabSettingsRequest,
    ConfigureLabSettingsResponse,
    ContactInfoBlock,
    EditLabProfileRequest,
    EditLabProfileResponse,
    FacilitiesBlock,
    LabInfoBlock,
    LabProfileActionResponse,
    LabProfileMeta,
    LabProfileResponse,
    LabProfileStats,
    LabSettingsBlock,
    OperationalHoursBlock,
    ServicesBlock,
    UserProfileBlock,
)


class LabProfileService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    async def get_profile(self, *, demo: bool = False) -> LabProfileResponse:
        row = None if demo else (await self.db.execute(select(LabProfileConfig).where(LabProfileConfig.hospital_id == self.hospital_id))).scalar_one_or_none()
        settings = LabSettingsBlock(
            auto_print_reports=True,
            email_notifications=True,
            sms_notifications=True,
            report_template="Standard",
        )
        return LabProfileResponse(
            meta=LabProfileMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=not demo,
                demo_data=demo,
            ),
            stats=LabProfileStats(total_tests=350, total_staff=45, equipment=78, branches=3),
            lab_information=LabInfoBlock(
                lab_id=row.lab_id if row else "LAB-001",
                lab_name=row.lab_name if row else "Advanced Diagnostic Laboratory",
                lab_type=row.lab_type if row else "Multi-Specialty Diagnostic Lab",
                registration_number=row.registration_number if row else "LAB/2024/001",
                established_date=row.established_date if row else "2015-06-15",
                accreditation=row.accreditation if row else "NABL Accredited",
                accreditation_number=row.accreditation_number if row else "NABL-12345",
            ),
            contact_information=ContactInfoBlock(
                address=row.address if row else "123 Medical Street, Healthcare City",
                city=row.city if row else "Mumbai",
                state=row.state if row else "Maharashtra",
                pincode=row.pincode if row else "400001",
                phone=row.phone if row else "+91 22 1234 5678",
                emergency_phone=row.emergency_phone if row else "+91 98 7654 3210",
                email=row.email if row else "info@advancedlab.com",
                website=row.website if row else "www.advancedlab.com",
            ),
            facilities=FacilitiesBlock(
                total_area_sqft=5000,
                departments=["Hematology", "Biochemistry", "Microbiology", "Histopathology", "Molecular Biology"],
                specialties=["Clinical Pathology", "Immunology", "Endocrinology", "Toxicology"],
                rooms=["Sample Collection (5)", "Testing Labs (8)", "Sterilization Room", "Storage Room", "Staff Room"],
            ),
            user_profile=UserProfileBlock(
                name="Dr. Rajesh Mehta",
                role="Lab Director",
                email="lab@dcm.demo",
                phone="+91 98 7654 3211",
                department="Laboratory Management",
                joined="2020-01-15",
                last_login="2024-01-15 10:30 AM",
                status="Active",
            ),
            operational_hours=OperationalHoursBlock(
                working_hours="24/7",
                weekdays="Mon-Sat: 6:00 AM - 10:00 PM",
                sunday="Sun: 7:00 AM - 8:00 PM",
                emergency="Available 24/7",
                home_collection="Available",
                report_delivery="Email, SMS, Portal, Physical",
            ),
            services=ServicesBlock(
                sample_types=["Blood", "Urine", "Stool", "CSF", "Tissue", "Swabs"],
                routine_tat="24-48 hours",
                urgent_tat="4-6 hours",
                stat_tat="2 hours",
            ),
            settings=settings,
        )

    async def edit_profile(self, payload: EditLabProfileRequest) -> EditLabProfileResponse:
        row = (await self.db.execute(select(LabProfileConfig).where(LabProfileConfig.hospital_id == self.hospital_id))).scalar_one_or_none()
        if not row:
            row = LabProfileConfig(
                hospital_id=self.hospital_id,
                lab_name=payload.lab_name,
                lab_type=payload.lab_type,
                registration_number=payload.registration_number,
                established_date=payload.established_date,
                accreditation=payload.accreditation,
                accreditation_number=payload.accreditation_number,
                lab_id="LAB-001",
            )
            self.db.add(row)
        else:
            row.lab_name = payload.lab_name
            row.lab_type = payload.lab_type
            row.registration_number = payload.registration_number
            row.established_date = payload.established_date
            row.accreditation = payload.accreditation
            row.accreditation_number = payload.accreditation_number
        await self.db.commit()
        return EditLabProfileResponse(
            message="Lab profile updated successfully.",
            updated_lab_name=payload.lab_name,
        )

    async def configure_settings(self, payload: ConfigureLabSettingsRequest) -> ConfigureLabSettingsResponse:
        return ConfigureLabSettingsResponse(
            message="Lab settings updated successfully.",
            settings=LabSettingsBlock(
                auto_print_reports=payload.auto_print_reports,
                email_notifications=payload.email_notifications,
                sms_notifications=payload.sms_notifications,
                report_template=payload.report_template,
            ),
        )

    async def change_password(self) -> ChangePasswordResponse:
        return ChangePasswordResponse(message="Password change flow initiated.")

    async def utility_action(self, action: str) -> LabProfileActionResponse:
        return LabProfileActionResponse(
            message=f"{action} action completed.",
            action=action,
        )

