"""
Demo request submissions (public marketing / DCM integration).
"""
from sqlalchemy import Column, String, Date, Text

from app.models.base import BaseModel
from app.core.database_types import JSON_TYPE


class DemoRequest(BaseModel):
    __tablename__ = "demo_requests"

    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=False)
    hospital_name = Column(String(255), nullable=False)
    role = Column(String(100), nullable=False)
    hospital_size = Column(String(255), nullable=True)
    preferred_demo_date = Column(Date, nullable=True)
    preferred_demo_mode = Column(String(50), nullable=True)
    modules = Column(JSON_TYPE, nullable=True)
    notes = Column(Text, nullable=True)

    def __repr__(self):
        return f"<DemoRequest(id={self.id}, email={self.email})>"
