"""
Support Tickets:
- Staff can create tickets -> goes to Hospital Admin email + dashboard.
- Hospital Admin can create tickets -> goes to Super Admin email + dashboard.
Both Hospital Admin and Super Admin can update ticket status; resolving sends email back to ticket raiser.
"""

import uuid
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy import select, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_hospital_admin_context, require_hospital_context, get_current_user
from app.core.config import settings
from app.core.enums import UserRole
from app.models.support import SupportTicket
from app.models.user import User, Role
from app.services.email_service import EmailService
from app.services.super_admin_service import SuperAdminService


router = APIRouter(prefix="/support", tags=["Support - Tickets"])


class SupportTicketCreateIn(BaseModel):
    subject: str
    description: str
    priority: str = "NORMAL"


class SupportTicketStatusUpdateIn(BaseModel):
    status: str
    resolution_notes: Optional[str] = None


async def _get_hospital_admin_emails(db: AsyncSession, hospital_id: uuid.UUID) -> Set[str]:
    r = await db.execute(
        select(User.email).where(
            User.hospital_id == hospital_id,
            User.roles.any(Role.name == UserRole.HOSPITAL_ADMIN.value),
            User.email.is_not(None),
        )
    )
    out: Set[str] = set()
    for e in r.scalars().all():
        if e:
            out.add(str(e).strip().lower())
    return out


async def _send_email_safe(to_email: str, subject: str, html: str, text: str) -> bool:
    try:
        return bool(await EmailService().send_email(to_email, subject, html, text))
    except Exception:
        return False


@router.post("/staff/tickets", status_code=status.HTTP_201_CREATED)
async def create_ticket_as_staff(
    body: SupportTicketCreateIn,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    ctx: Dict[str, Any] = Depends(require_hospital_context),
):
    # Allow any hospital-scoped staff (including doctors/nurses/receptionists) to raise a ticket.
    hospital_id: uuid.UUID = ctx["hospital_id"]
    if not hospital_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "MISSING_HOSPITAL", "message": "Hospital context missing"})

    service = SuperAdminService(db)
    result = await service.create_support_ticket(hospital_id, current_user.id, body.subject, body.description, body.priority)

    # Email hospital admins (and only them) when staff raises a ticket.
    recipients = await _get_hospital_admin_emails(db, hospital_id)
    ticket_id = result.get("ticket_id")
    priority = (body.priority or "NORMAL").strip().upper()
    sent = 0
    for rcp in recipients:
        ok = await _send_email_safe(
            rcp,
            f"New Staff Support Ticket - {ticket_id}",
            f"""
            <p>Hello Hospital Admin,</p>
            <p>A staff member raised a support ticket.</p>
            <p><b>Ticket ID:</b> {ticket_id}</p>
            <p><b>Subject:</b> {body.subject}</p>
            <p><b>Description:</b> {body.description}</p>
            <p><b>Priority:</b> {priority}</p>
            <p>Please review and update the status in your dashboard.</p>
            """,
            f"New staff ticket {ticket_id}\nSubject: {body.subject}\nPriority: {priority}\n\n{body.description}",
        )
        sent += 1 if ok else 0

    result["email_sent"] = bool(sent)
    result["email_recipients"] = sent
    return result


@router.post("/hospital-admin/tickets", status_code=status.HTTP_201_CREATED)
async def create_ticket_as_hospital_admin(
    body: SupportTicketCreateIn,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    ctx: Dict[str, Any] = Depends(require_hospital_admin_context()),
):
    hospital_id: uuid.UUID = ctx["hospital_id"]
    service = SuperAdminService(db)
    result = await service.create_support_ticket(hospital_id, current_user.id, body.subject, body.description, body.priority)

    # Email Super Admin when hospital admin raises a ticket.
    to_email = (getattr(settings, "SUPERADMIN_EMAIL", None) or getattr(settings, "EMAIL_FROM", None) or "").strip()
    ticket_id = result.get("ticket_id")
    priority = (body.priority or "NORMAL").strip().upper()
    if to_email:
        ok = await _send_email_safe(
            to_email,
            f"New Hospital Admin Support Ticket - {ticket_id}",
            f"""
            <p>Hello Super Admin,</p>
            <p>A hospital admin raised a support ticket.</p>
            <p><b>Ticket ID:</b> {ticket_id}</p>
            <p><b>Subject:</b> {body.subject}</p>
            <p><b>Description:</b> {body.description}</p>
            <p><b>Priority:</b> {priority}</p>
            """,
            f"New hospital admin ticket {ticket_id}\nSubject: {body.subject}\nPriority: {priority}\n\n{body.description}",
        )
        result["email_sent"] = bool(ok)
        result["email_recipients"] = 1 if ok else 0
    else:
        result["email_sent"] = False
        result["email_recipients"] = 0
    return result


@router.get("/hospital-admin/tickets")
async def list_tickets_for_hospital_admin(
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    ctx: Dict[str, Any] = Depends(require_hospital_admin_context()),
):
    hospital_id: uuid.UUID = ctx["hospital_id"]
    conditions = [SupportTicket.hospital_id == hospital_id]
    if status_filter:
        conditions.append(SupportTicket.status == status_filter)
    q = (
        select(SupportTicket)
        .where(*conditions)
        .order_by(desc(SupportTicket.created_at))
        .offset(skip)
        .limit(limit)
    )
    r = await db.execute(q)
    tickets = r.scalars().all()
    return {
        "tickets": [
            {
                "id": str(t.id),
                "hospital_id": str(t.hospital_id),
                "raised_by_user_id": str(t.raised_by_user_id),
                "subject": t.subject,
                "description": t.description,
                "status": t.status,
                "priority": t.priority,
                "resolution_notes": t.resolution_notes,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tickets
        ],
        "skip": skip,
        "limit": limit,
    }


@router.get("/hospital-admin/tickets/completed")
async def list_completed_tickets_for_hospital_admin(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    ctx: Dict[str, Any] = Depends(require_hospital_admin_context()),
):
    hospital_id: uuid.UUID = ctx["hospital_id"]
    q = (
        select(SupportTicket)
        .where(
            SupportTicket.hospital_id == hospital_id,
            or_(SupportTicket.status == "RESOLVED", SupportTicket.status == "CLOSED"),
        )
        .order_by(desc(SupportTicket.updated_at), desc(SupportTicket.created_at))
        .offset(skip)
        .limit(limit)
    )
    r = await db.execute(q)
    tickets = r.scalars().all()
    return {
        "tickets": [
            {
                "id": str(t.id),
                "hospital_id": str(t.hospital_id),
                "raised_by_user_id": str(t.raised_by_user_id),
                "subject": t.subject,
                "description": t.description,
                "status": t.status,
                "priority": t.priority,
                "resolution_notes": t.resolution_notes,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tickets
        ],
        "skip": skip,
        "limit": limit,
    }


@router.patch("/hospital-admin/tickets/{ticket_id}/status")
async def update_ticket_status_as_hospital_admin(
    ticket_id: str,
    body: SupportTicketStatusUpdateIn,
    db: AsyncSession = Depends(get_db_session),
    ctx: Dict[str, Any] = Depends(require_hospital_admin_context()),
):
    hospital_id: uuid.UUID = ctx["hospital_id"]
    try:
        t_uuid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_TICKET_ID", "message": "Invalid ticket ID format"})

    r = await db.execute(select(SupportTicket).where(SupportTicket.id == t_uuid, SupportTicket.hospital_id == hospital_id).limit(1))
    ticket = r.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "TICKET_NOT_FOUND", "message": "Support ticket not found"})

    service = SuperAdminService(db)
    result = await service.update_support_ticket_status(t_uuid, body.status, resolution_notes=body.resolution_notes)

    if str(body.status).upper() in {"RESOLVED", "CLOSED"}:
        try:
            user_r = await db.execute(select(User.email).where(User.id == ticket.raised_by_user_id).limit(1))
            email = user_r.scalar_one_or_none()
            if email:
                notes = body.resolution_notes or ticket.resolution_notes or ""
                await _send_email_safe(
                    str(email),
                    f"Support Ticket {ticket.id} marked {str(body.status).upper()}",
                    f"""
                    <p>Hello,</p>
                    <p>Your support ticket has been updated.</p>
                    <p><b>Ticket ID:</b> {ticket.id}</p>
                    <p><b>Status:</b> {str(body.status).upper()}</p>
                    <p><b>Notes:</b> {notes}</p>
                    """,
                    f"Ticket {ticket.id} status: {str(body.status).upper()}\nNotes: {notes}",
                )
        except Exception:
            pass

    return result

