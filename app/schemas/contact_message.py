"""Pydantic schema for public contact-us form."""
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class ContactMessageCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: Optional[str] = Field(None, min_length=3, max_length=50)
    hospital_name: Optional[str] = Field(None, max_length=255)
    message: str = Field(..., min_length=1)

    @field_validator("full_name", "message", mode="before")
    @classmethod
    def required_non_empty(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v

    @field_validator("phone", "hospital_name", mode="before")
    @classmethod
    def normalize_optional(cls, v):
        if v is None or v == "":
            return None
        return v.strip() if isinstance(v, str) else v
