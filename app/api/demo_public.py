"""
Public demo request API (DCM / marketing site).
POST /demo/request — no authentication.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.models.demo_request import DemoRequest
from app.schemas.demo_request import DemoRequestCreate
from app.services.email_service import EmailService
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["Demo"])


_DEMO_REQUEST_OPENAPI_EXAMPLE = {
    "full_name": "John Smith",
    "email": "john@hospital.com",
    "phone": "+919876543210",
    "hospital_name": "City Care Hospital",
    "role": "Doctor",
    "hospital_size": "50 beds / 120 staff",
    "preferred_demo_date": "10-04-2026",
    "preferred_demo_mode": "Online",
    "modules": ["Patient Management", "Appointments"],
    "notes": "We want to focus on billing workflow",
}


def _parse_demo_date(s: str):
    from datetime import date as date_cls

    day_s, month_s, year_s = s.split("-")
    return date_cls(int(year_s), int(month_s), int(day_s))


@router.post(
    "/request",
    summary="Submit demo request",
    description=(
        "Public endpoint for hospital/clinic demo requests (marketing form / DCM). "
        "Required: full_name, email, phone, hospital_name, role. "
        "Optional: preferred_demo_date as DD-MM-YYYY or YYYY-MM-DD."
    ),
    response_model=None,
)
async def submit_demo_request(
    db: AsyncSession = Depends(get_db_session),
    payload: DemoRequestCreate = Body(
        ...,
        openapi_examples={
            "default": {
                "summary": "Full example",
                "value": _DEMO_REQUEST_OPENAPI_EXAMPLE,
            },
            "minimal": {
                "summary": "Required fields only",
                "value": {
                    "full_name": "Jane Doe",
                    "email": "jane@clinic.com",
                    "phone": "+919999999999",
                    "hospital_name": "Metro Clinic",
                    "role": "Hospital Admin",
                },
            },
        },
    ),
):
    try:
        demo_date = (
            _parse_demo_date(payload.preferred_demo_date)
            if payload.preferred_demo_date
            else None
        )

        row = DemoRequest(
            full_name=payload.full_name,
            email=str(payload.email).lower().strip(),
            phone=payload.phone,
            hospital_name=payload.hospital_name,
            role=payload.role,
            hospital_size=payload.hospital_size,
            preferred_demo_date=demo_date,
            preferred_demo_mode=payload.preferred_demo_mode,
            modules=payload.modules or [],
            notes=payload.notes,
        )
        db.add(row)
        await db.commit()
    except Exception as e:
        logger.exception("demo request DB save failed: %s", e)
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal server error"},
        )

    notify_to = (settings.DEMO_REQUEST_NOTIFY_EMAIL or "").strip() or (
        settings.SUPERADMIN_EMAIL or ""
    ).strip() or settings.EMAIL_FROM

    email_service = EmailService()
    try:
        modules_html = (
            "<ul>" + "".join(f"<li>{m}</li>" for m in (payload.modules or [])) + "</ul>"
            if payload.modules
            else "<p><i>None selected</i></p>"
        )
        admin_html = f"""
        <h2>New demo request</h2>
        <table style="border-collapse:collapse">
          <tr><td><b>Name</b></td><td>{payload.full_name}</td></tr>
          <tr><td><b>Email</b></td><td>{payload.email}</td></tr>
          <tr><td><b>Phone</b></td><td>{payload.phone}</td></tr>
          <tr><td><b>Hospital</b></td><td>{payload.hospital_name}</td></tr>
          <tr><td><b>Role</b></td><td>{payload.role}</td></tr>
          <tr><td><b>Size</b></td><td>{payload.hospital_size or '—'}</td></tr>
          <tr><td><b>Preferred date</b></td><td>{payload.preferred_demo_date or '—'}</td></tr>
          <tr><td><b>Mode</b></td><td>{payload.preferred_demo_mode or '—'}</td></tr>
        </table>
        <h3>Modules</h3>
        {modules_html}
        <h3>Notes</h3>
        <p>{payload.notes or '—'}</p>
        <p><small>Submitted at {datetime.utcnow().isoformat()}Z</small></p>
        """
        admin_text = (
            f"Demo request from {payload.full_name} ({payload.email})\n"
            f"Hospital: {payload.hospital_name}\nPhone: {payload.phone}\n"
        )
        await email_service.send_email(
            notify_to,
            f"[Demo request] {payload.hospital_name} — {payload.full_name}",
            admin_html,
            admin_text,
        )
    except Exception as e:
        logger.warning("Demo request admin email failed (record saved): %s", e)

    if settings.DEMO_REQUEST_SEND_CONFIRMATION:
        try:
            confirm_html = f"""
            <p>Hi {payload.full_name},</p>
            <p>Thank you for your interest. We received your demo request for <b>{payload.hospital_name}</b>
            and will contact you soon.</p>
            <p>Best regards,<br/>Hospital Management Team</p>
            """
            await email_service.send_email(
                str(payload.email),
                "We received your demo request",
                confirm_html,
                f"Thank you {payload.full_name}. We received your demo request and will be in touch.",
            )
        except Exception as e:
            logger.warning("Demo request confirmation email failed: %s", e)

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": "Demo request submitted successfully",
        },
    )
