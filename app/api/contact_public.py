"""
Public contact-us API (DCM / marketing site).
POST /contact/send — no authentication.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_db_session
from app.models.contact_message import ContactMessage
from app.schemas.contact_message import ContactMessageCreate
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contact", tags=["Contact"])

_CONTACT_OPENAPI_EXAMPLE = {
    "full_name": "John Smith",
    "email": "john.smith@hospital.com",
    "phone": "+919876543210",
    "hospital_name": "City Care Hospital",
    "message": "We are interested in a demo and want to understand billing and lab modules.",
}


@router.post(
    "/send",
    summary="Send contact-us message",
    description="Public endpoint for contact form submissions from website/DCM.",
    response_model=None,
)
async def send_contact_message(
    db: AsyncSession = Depends(get_db_session),
    payload: ContactMessageCreate = Body(
        ...,
        openapi_examples={
            "default": {"summary": "Full example", "value": _CONTACT_OPENAPI_EXAMPLE},
            "minimal": {
                "summary": "Required fields only",
                "value": {
                    "full_name": "Jane Doe",
                    "email": "jane@clinic.com",
                    "message": "Please contact us for onboarding details.",
                },
            },
        },
    ),
):
    try:
        row = ContactMessage(
            full_name=payload.full_name,
            email=str(payload.email).strip().lower(),
            phone=payload.phone,
            hospital_name=payload.hospital_name,
            message=payload.message,
        )
        db.add(row)
        await db.commit()
    except Exception as e:
        logger.exception("contact message DB save failed: %s", e)
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal server error"},
        )

    notify_to = (settings.CONTACT_MESSAGE_NOTIFY_EMAIL or "").strip() or (
        settings.SUPERADMIN_EMAIL or ""
    ).strip() or settings.EMAIL_FROM

    email_service = EmailService()
    try:
        admin_html = f"""
        <h2>New contact message</h2>
        <table style="border-collapse:collapse">
          <tr><td><b>Name</b></td><td>{payload.full_name}</td></tr>
          <tr><td><b>Email</b></td><td>{payload.email}</td></tr>
          <tr><td><b>Phone</b></td><td>{payload.phone or '—'}</td></tr>
          <tr><td><b>Hospital</b></td><td>{payload.hospital_name or '—'}</td></tr>
          <tr><td><b>Message</b></td><td>{payload.message}</td></tr>
        </table>
        <p><small>Submitted at {datetime.utcnow().isoformat()}Z</small></p>
        """
        admin_text = (
            f"Contact message from {payload.full_name} ({payload.email})\n"
            f"Phone: {payload.phone or '-'}\n"
            f"Hospital: {payload.hospital_name or '-'}\n"
            f"Message: {payload.message}\n"
        )
        await email_service.send_email(
            notify_to,
            f"[Contact] {payload.full_name}",
            admin_html,
            admin_text,
        )
    except Exception as e:
        logger.warning("Contact admin email failed (record saved): %s", e)

    if settings.CONTACT_MESSAGE_SEND_ACK:
        try:
            ack_html = f"""
            <p>Hi {payload.full_name},</p>
            <p>Thank you for contacting us. Our team received your message and will reach out soon.</p>
            <p>Best regards,<br/>Hospital Management Team</p>
            """
            await email_service.send_email(
                str(payload.email),
                "We received your message",
                ack_html,
                "Thank you for contacting us. Our team will reach out soon.",
            )
        except Exception as e:
            logger.warning("Contact acknowledgment email failed: %s", e)

    return JSONResponse(
        status_code=200,
        content={"success": True, "message": "Message sent successfully"},
    )
