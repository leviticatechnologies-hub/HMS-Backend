"""
Root-level notifications compatibility endpoints.
Implements internal endpoint:
POST /notifications/ticket-email
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.api.deps import get_current_user, require_hospital_context
from app.database.session import get_db_session
from app.models.user import User, Role
from app.core.enums import UserRole
from app.schemas.notifications.ticket_email import TicketEmailRequest
from app.services.email_service import EmailService

router = APIRouter(prefix="/notifications", tags=["Notifications"])
logger = logging.getLogger(__name__)


def _render_ticket_email_html(req: TicketEmailRequest) -> tuple[str, str, str]:
    email_subject = f"New Support Ticket Created - {req.ticket_id}"
    html = f"""
    <p>Hello,</p>
    <p>A new support ticket has been created for your hospital.</p>
    <p><b>Ticket ID:</b> {req.ticket_id}</p>
    <p><b>Subject:</b> {req.subject}</p>
    <p><b>Description:</b> {req.description}</p>
    <p><b>Priority:</b> {req.priority}</p>
    <p>Our support team will get back to you shortly.</p>
    <p>Regards,<br/>Support Team</p>
    """
    text = (
        "Hello,\n\n"
        "A new support ticket has been created for your hospital.\n\n"
        f"Ticket ID: {req.ticket_id}\n"
        f"Subject: {req.subject}\n"
        f"Description: {req.description}\n"
        f"Priority: {req.priority}\n\n"
        "Our support team will get back to you shortly.\n\n"
        "Regards,\nSupport Team\n"
    )
    return email_subject, html, text


@router.post("/ticket-email")
async def ticket_email_root(
    body: TicketEmailRequest,
    current_user=Depends(get_current_user),
    context=Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Internal endpoint: send support ticket notification email to the hospital.
    Auth required.
    """
    _ = current_user
    _ = context

    hospital_id = context.get("hospital_id")
    hospital_uuid = UUID(str(hospital_id)) if hospital_id else None

    recipients_set: set[str] = set()
    if body.hospital_email:
        recipients_set.add(str(body.hospital_email).strip().lower())

    for e in body.additional_emails:
        if e:
            recipients_set.add(str(e).strip().lower())

    # Include all hospital admins emails too.
    if hospital_uuid:
        r = await db.execute(
            select(User.email).where(
                User.hospital_id == hospital_uuid,
                User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN.value),
                User.email.is_not(None),
            )
        )
        for email in r.scalars().all():
            if email:
                recipients_set.add(str(email).strip().lower())

    recipients: list[str] = sorted(recipients_set)

    email_service = EmailService()
    try:
        subject, html, text = _render_ticket_email_html(body)
        for recipient in recipients:
            await email_service.send_email(recipient, subject, html, text)
    except Exception as e:
        logger.exception("Failed to send ticket email: %s", e)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to send email"},
        )

    return {"success": True, "message": "Email sent successfully"}

