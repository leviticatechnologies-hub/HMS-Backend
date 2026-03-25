"""
Notification API: providers, OTP, bulk SMS, unified send, preferences, history, schedule, queue.
RBAC: staff = hospital-scoped; patients = own preferences/history; super-admin = platform.
"""
from datetime import datetime
from uuid import UUID
from typing import Optional, List

import logging

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.api.deps import (
    get_current_user,
    require_hospital_context,
    require_hospital_admin,
)
from app.core.enums import UserRole
from app.models.user import User, Role
from app.services.notifications import NotificationService
from app.schemas.notifications import (
    NotificationProviderResponse,
    NotificationProviderStatusUpdate,
    NotificationProviderConfigUpdate,
    NotificationProviderTestRequest,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationJobResponse,
    NotificationJobDetailResponse,
    NotificationSendRequest,
    NotificationScheduleRequest,
    OtpSendRequest,
    OtpVerifyRequest,
    BulkSmsRequest,
    NotificationHistoryFilters,
    NotificationQueueQuery,
    TicketEmailRequest,
)
from app.services.email_service import EmailService
from sqlalchemy import select

router = APIRouter(prefix="/notifications", tags=["Notifications"])
logger = logging.getLogger(__name__)


def _hospital_id_from_context(context: dict) -> UUID:
    from uuid import UUID
    hid = context.get("hospital_id")
    if not hid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hospital context required")
    return UUID(hid) if isinstance(hid, str) else hid


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
async def ticket_email(
    body: TicketEmailRequest,
    current_user: User = Depends(get_current_user),
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Internal endpoint: send support ticket notification email to the hospital.
    Auth required (JWT). Hospital context required (tenant isolation).
    """
    _ = current_user
    _ = db
    _ = context

    hospital_id = _hospital_id_from_context(context)

    # Recipients: mandatory hospital email + any additional emails + hospital admin emails.
    recipients_set: set[str] = set()
    if body.hospital_email:
        recipients_set.add(str(body.hospital_email).strip().lower())

    for e in body.additional_emails:
        if e:
            recipients_set.add(str(e).strip().lower())

    # Add all hospital admin emails.
    r = await db.execute(
        select(User.email).where(
            User.hospital_id == hospital_id,
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
            await email_service.send_email(
                recipient,
                subject,
                html,
                text,
            )
    except Exception as e:
        # Don't leak internals; log for debugging.
        logger.exception("Failed to send ticket email: %s", e)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": "Failed to send email"},
        )

    return {"success": True, "message": "Email sent successfully"}


# ---------- Providers ----------
@router.get("/providers", response_model=List[NotificationProviderResponse])
async def list_providers(
    provider_type: Optional[str] = Query(None, description="EMAIL or SMS"),
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """List notification providers for the hospital (including global defaults)."""
    hospital_id = _hospital_id_from_context(context)
    repo = NotificationService(db, hospital_id).repo
    providers = await repo.list_providers(provider_type=provider_type, include_global=True)
    return providers


@router.patch("/providers/{provider_id}/status", response_model=NotificationProviderResponse)
async def update_provider_status(
    provider_id: UUID,
    body: NotificationProviderStatusUpdate,
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Enable/disable a provider."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    provider = await svc.repo.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    provider.is_enabled = body.is_enabled
    await svc.repo.update_provider(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


@router.put("/providers/{provider_id}/config", response_model=NotificationProviderResponse)
async def update_provider_config(
    provider_id: UUID,
    body: NotificationProviderConfigUpdate,
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Update provider config (store encrypted in production)."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    provider = await svc.repo.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    setattr(provider, "config_", body.config)
    await svc.repo.update_provider(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


@router.post("/providers/{provider_id}/test")
async def test_provider(
  provider_id: UUID,
  body: NotificationProviderTestRequest,
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Send a test email/SMS to the given address."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    provider = await svc.repo.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    key = f"test:{provider_id}:{body.to_address}:{datetime.utcnow().isoformat()}"
    job = await svc.send(
        channel=provider.provider_type,
        to=body.to_address,
        idempotency_key=key,
        event_type="GENERAL",
        raw_message="This is a test message from HSM Notifications.",
        subject="HSM Test Notification",
    )
    return {"job_id": str(job.id), "message": "Test job queued. Delivery is async."}


# ---------- OTP ----------
@router.post("/otp/send")
async def otp_send(
    body: OtpSendRequest,
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Send OTP to phone (rate-limited per phone)."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    try:
        result = await svc.otp_send(phone=body.phone, purpose=body.purpose or "LOGIN")
        await db.commit()
        return result
    except ValueError as e:
        if "rate limit" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/otp/verify")
async def otp_verify(
  body: OtpVerifyRequest,
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Verify OTP for phone."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    ok = await svc.otp_verify(phone=body.phone, otp=body.otp, purpose="LOGIN")
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")
    return {"verified": True}


# ---------- Bulk SMS (staff-only) ----------
@router.post("/sms/bulk", response_model=List[NotificationJobResponse])
async def bulk_sms(
  body: BulkSmsRequest,
  current_user: User = Depends(require_hospital_admin()),
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Bulk SMS to a list of phones (Hospital Admin / authorized staff)."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    jobs = await svc.bulk_sms(
        phones=body.phones,
        message=body.message,
        idempotency_key=body.idempotency_key,
        created_by_user_id=current_user.id,
    )
    await db.commit()
    return jobs


# ---------- Unified send ----------
@router.post("/send", response_model=NotificationJobResponse)
async def send(
  body: NotificationSendRequest,
  current_user: User = Depends(get_current_user),
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Unified multi-channel send (outbox: job created immediately, delivery async)."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    if not body.template_key and not body.raw_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide template_key or raw_message",
        )
    job = await svc.send(
        channel=body.channel,
        to=body.to,
        idempotency_key=body.idempotency_key,
        event_type=body.event_type or "GENERAL",
        template_key=body.template_key,
        raw_message=body.raw_message,
        subject=body.subject,
        payload=body.payload or {},
        created_by_user_id=current_user.id,
    )
    await db.commit()
    await db.refresh(job)
    return job


# ---------- Preferences ----------
@router.get("/preferences/me", response_model=Optional[NotificationPreferenceResponse])
async def get_preferences_me(
  current_user: User = Depends(get_current_user),
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Get current user's notification preferences."""
    hospital_id = _hospital_id_from_context(context)
    role_names = [r.name for r in (current_user.roles or [])]
    owner_type = "PATIENT" if UserRole.PATIENT.value in role_names else "STAFF"
    svc = NotificationService(db, hospital_id)
    pref = await svc.get_preferences_me(owner_type=owner_type, owner_id=current_user.id)
    return pref


@router.put("/preferences/me", response_model=NotificationPreferenceResponse)
async def update_preferences_me(
  body: NotificationPreferenceUpdate,
  current_user: User = Depends(get_current_user),
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Update current user's notification preferences."""
    hospital_id = _hospital_id_from_context(context)
    role_names = [r.name for r in (current_user.roles or [])]
    owner_type = "PATIENT" if UserRole.PATIENT.value in role_names else "STAFF"
    svc = NotificationService(db, hospital_id)
    pref = await svc.upsert_preferences_me(
        owner_type=owner_type,
        owner_id=current_user.id,
        email_enabled=body.email_enabled if body.email_enabled is not None else True,
        sms_enabled=body.sms_enabled if body.sms_enabled is not None else True,
        whatsapp_enabled=body.whatsapp_enabled if body.whatsapp_enabled is not None else False,
        quiet_hours_start=body.quiet_hours_start,
        quiet_hours_end=body.quiet_hours_end,
    )
    await db.commit()
    await db.refresh(pref)
    return pref


@router.get("/preferences/{owner_type}/{owner_id}", response_model=Optional[NotificationPreferenceResponse])
async def get_preferences_admin(
  owner_type: str,
  owner_id: UUID,
  current_user: User = Depends(require_hospital_admin()),
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Get notification preferences for a user (admin)."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    pref = await svc.get_preferences_me(owner_type=owner_type, owner_id=owner_id)
    return pref


# ---------- History ----------
@router.get("/history", response_model=List[NotificationJobResponse])
async def list_history(
  status: Optional[str] = Query(None),
  from_date: Optional[datetime] = Query(None, alias="from"),
  to_date: Optional[datetime] = Query(None, alias="to"),
  event_type: Optional[str] = Query(None),
  skip: int = Query(0, ge=0),
  limit: int = Query(50, ge=1, le=100),
  current_user: User = Depends(get_current_user),
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """List notification jobs (history). Patients see only their own (to_address = their email/phone)."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    to_address_in = None
    role_names = [r.name for r in (current_user.roles or [])]
    if UserRole.PATIENT.value in role_names:
        to_address_in = [current_user.email]
        if getattr(current_user, "phone", None):
            to_address_in.append(current_user.phone)
    jobs = await svc.list_history(
        status=status,
        from_ts=from_date,
        to_ts=to_date,
        event_type=event_type,
        to_address_in=to_address_in,
        skip=skip,
        limit=limit,
    )
    return jobs


@router.get("/jobs/{job_id}", response_model=Optional[NotificationJobDetailResponse])
async def get_job(
  job_id: UUID,
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Get a single notification job with delivery logs."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    job = await svc.get_job(job_id)
    return job


# ---------- Schedule ----------
@router.post("/schedule", response_model=NotificationJobResponse)
async def schedule(
  body: NotificationScheduleRequest,
  current_user: User = Depends(get_current_user),
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Schedule a notification for later delivery."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    job = await svc.schedule(
        event_type=body.event_type,
        channel=body.channel,
        to=body.to,
        scheduled_for=body.scheduled_for,
        idempotency_key=body.idempotency_key,
        template_key=body.template_key,
        payload=body.payload or {},
        created_by_user_id=current_user.id,
    )
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{job_id}/cancel", response_model=Optional[NotificationJobResponse])
async def cancel_job(
  job_id: UUID,
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Cancel a queued job."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    job = await svc.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or not cancellable")
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{job_id}/retry", response_model=Optional[NotificationJobResponse])
async def retry_job(
  job_id: UUID,
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """Retry a failed job."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    job = await svc.retry_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or not retriable")
    await db.commit()
    await db.refresh(job)
    return job


# ---------- Queue ----------
@router.get("/queue", response_model=List[NotificationJobResponse])
async def list_queue(
  status: str = Query("QUEUED", description="QUEUED or FAILED"),
  limit: int = Query(50, ge=1, le=200),
  context: dict = Depends(require_hospital_context),
  db: AsyncSession = Depends(get_db_session),
):
    """List queued or failed jobs (for admin/monitoring)."""
    hospital_id = _hospital_id_from_context(context)
    svc = NotificationService(db, hospital_id)
    jobs = await svc.list_queue(status=status, limit=limit)
    return jobs
