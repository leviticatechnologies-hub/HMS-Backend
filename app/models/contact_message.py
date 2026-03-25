"""
Public contact-us messages (website / DCM integration).
"""
from sqlalchemy import Column, String, Text

from app.models.base import BaseModel


class ContactMessage(BaseModel):
    __tablename__ = "contact_messages"

    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=True)
    hospital_name = Column(String(255), nullable=True)
    message = Column(Text, nullable=False)

    def __repr__(self):
        return f"<ContactMessage(id={self.id}, email={self.email})>"
