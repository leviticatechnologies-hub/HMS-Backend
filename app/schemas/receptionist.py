"""Pydantic schemas for receptionist self-service profile API."""

from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class ReceptionistProfileSelfUpdate(BaseModel):
    """Fields a receptionist may update on their own profile (user + receptionist_profiles)."""

    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    employee_id: Optional[str] = Field(None, max_length=100)
    work_area: Optional[str] = Field(None, max_length=100)
    shift_type: Optional[str] = Field(
        None,
        max_length=20,
        description="DAY, NIGHT, or ROTATING",
    )
    employment_type: Optional[str] = Field(
        None,
        max_length=20,
        description="FULL_TIME, PART_TIME, or CONTRACT",
    )
    experience_years: Optional[int] = Field(None, ge=0, le=60)
    designation: Optional[str] = Field(None, max_length=100)
    avatar_url: Optional[str] = Field(
        None,
        max_length=500,
        description="Profile photo URL (stored as user.avatar_url)",
    )
    gender: Optional[str] = Field(None, max_length=30)
    blood_group: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = Field(None, max_length=2000)
    shift_timing: Optional[str] = Field(
        None,
        max_length=200,
        description="Human-readable shift label (stored in user_metadata)",
    )
    joining_date: Optional[str] = Field(
        None,
        description="ISO date string YYYY-MM-DD (stored in user_metadata)",
    )

    @field_validator(
        "gender",
        "blood_group",
        "address",
        "shift_timing",
        "joining_date",
        "avatar_url",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v
