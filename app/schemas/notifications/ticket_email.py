"""Request schema for sending ticket-email notification to hospital."""

from typing import Optional, List

from pydantic import BaseModel, EmailStr, Field, field_validator
from pydantic import ConfigDict


class TicketEmailRequest(BaseModel):
    """
    Internal request: send email notification when a support ticket is created.

    DCM can send:
    - hospital_id (optional for our logic, but validated)
    - hospital_email (mandatory)
    - ticket_id/subject/description/priority
    - additional_emails (optional)
    """

    model_config = ConfigDict(extra="ignore")

    hospital_id: Optional[str] = Field(default=None, description="Hospital ID (UUID string).")
    hospital_email: EmailStr = Field(..., description="Hospital registered email address (To).")

    ticket_id: str = Field(..., min_length=1, max_length=120)
    subject: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., min_length=1, max_length=5000)

    priority: str = Field(..., description="LOW, NORMAL, HIGH, URGENT")
    created_by: Optional[str] = Field(default=None, description="Creator name (optional).")
    additional_emails: List[EmailStr] = Field(default_factory=list)

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        s = (v or "").strip().upper()
        allowed = {"LOW", "NORMAL", "HIGH", "URGENT"}
        if s not in allowed:
            raise ValueError(f"priority must be one of {sorted(allowed)}")
        return s

    @field_validator("ticket_id", "subject", "description", mode="before")
    @classmethod
    def strip_required(cls, v):
        if isinstance(v, str):
            s = v.strip()
            return s
        return v

