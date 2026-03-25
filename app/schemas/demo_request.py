"""Pydantic schemas for public demo request API."""
import re
from datetime import date
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


_DATE_DD_MM_YYYY = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DemoRequestCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: str = Field(..., min_length=3, max_length=50)
    hospital_name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(..., min_length=1, max_length=100)
    hospital_size: Optional[str] = Field(None, max_length=255)
    preferred_demo_date: Optional[str] = None
    preferred_demo_mode: Optional[str] = Field(None, max_length=50)
    modules: Optional[List[str]] = None
    notes: Optional[str] = None

    @field_validator("full_name", "phone", "hospital_name", "role", mode="before")
    @classmethod
    def strip_strings(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v

    @field_validator("hospital_size", "preferred_demo_mode", "notes", mode="before")
    @classmethod
    def strip_optional(cls, v):
        if v is None or v == "":
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("preferred_demo_date", mode="before")
    @classmethod
    def normalize_demo_date(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            return s
        return v

    @field_validator("preferred_demo_date")
    @classmethod
    def validate_demo_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if _DATE_DD_MM_YYYY.match(v):
            day_s, month_s, year_s = v.split("-")
            d, m, y = int(day_s), int(month_s), int(year_s)
            try:
                date(y, m, d)
            except ValueError:
                raise ValueError("Preferred demo date is not a valid calendar date")
            return v
        if _DATE_ISO.match(v):
            year_s, month_s, day_s = v.split("-")
            y, m, d = int(year_s), int(month_s), int(day_s)
            try:
                date(y, m, d)
            except ValueError:
                raise ValueError("Preferred demo date is not a valid calendar date")
            return f"{day_s.zfill(2)}-{month_s.zfill(2)}-{year_s}"
        raise ValueError(
            "Preferred demo date must be DD-MM-YYYY (e.g. 10-04-2026) or YYYY-MM-DD (e.g. 2026-04-10)"
        )

    @field_validator("modules", mode="before")
    @classmethod
    def normalize_modules(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return v
