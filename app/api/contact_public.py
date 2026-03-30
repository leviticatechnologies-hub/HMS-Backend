# contact_public.py

"""
Public contact-us API (DCM / marketing site).
POST /contact/send — no authentication.
"""
import logging
import asyncio
from datetime import datetime

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
import os

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


@router.get("/health", include_in_schema=True)
async def contact_health_check():
    """Check if contact service is properly configured"""
    checks = {
        "status": "operational",
        "email_provider": "SendGrid SMTP",
        "smtp_configured": bool(settings.SMTP_USER and settings.SMTP_PASS),
        "smtp_host": settings.SMTP_HOST,
        "smtp_user": settings.SMTP_USER,
        "email_from": settings.EMAIL_FROM,
        "notify_email": settings.CONTACT_MESSAGE_NOTIFY_EMAIL or settings.SUPERADMIN_EMAIL or settings.EMAIL_FROM,
    }
    
    if not checks["smtp_configured"]:
        logger.warning("SMTP credentials not configured")
        checks["status"] = "degraded"
        checks["warning"] = "SMTP_USER and SMTP_PASS not set"
    
    return JSONResponse(content=checks)


async def send_email_safe(
    email_service: EmailService, 
    to_email: str, 
    subject: str, 
    html: str, 
    text: str, 
    timeout: int = 15
) -> bool:
    """
    Safely send email with timeout protection.
    Returns True if successful, False otherwise.
    Never raises exceptions.
    """
    try:
        result = await asyncio.wait_for(
            email_service.send_email(to_email, subject, html, text),
            timeout=timeout
        )
        return result
    except asyncio.TimeoutError:
        logger.error(f"Email to {to_email} timed out after {timeout}s")
        return False
    except Exception as e:
        logger.error(f"Email to {to_email} failed: {type(e).__name__}: {str(e)}")
        return False


@router.post("/test-email", include_in_schema=True)
async def test_email_sending(test_email: str = "kiranios456@gmail.com"):
    """Test SendGrid email sending (for debugging)"""
    logger.info(f"Testing SendGrid email to {test_email}")
    
    if not settings.SENDGRID_API_KEY:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "SENDGRID_API_KEY not configured in environment variables"
            }
        )
    
    email_service = EmailService()
    
    test_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; border-radius: 10px;">
            <h1 style="color: white;">✅ Test Email Success!</h1>
        </div>
        <div style="padding: 20px;">
            <h2>SendGrid Configuration Working</h2>
            <p>This is a test email from your Hospital Management System.</p>
            <p>If you received this, SendGrid is configured correctly! 🎉</p>
        </div>
    </body>
    </html>
    """
    
    test_text = "Test email from Hospital Management System. If you received this, SendGrid is working correctly!"
    
    result = await send_email_safe(
        email_service,
        test_email,
        "🧪 Test Email - Hospital Management System",
        test_html,
        test_text,
        timeout=20
    )
    
    return JSONResponse(
        status_code=200 if result else 500,
        content={
            "success": result,
            "message": "Test email sent successfully! Check your inbox." if result else "Test email failed. Check logs for details.",
            "config": {
                "provider": "SendGrid",
                "api_key_set": bool(settings.SENDGRID_API_KEY),
                "email_from": settings.EMAIL_FROM,
            }
        }
    )


@router.post("/send", summary="Send contact-us message")
async def send_contact_message(
    db: AsyncSession = Depends(get_db_session),
    payload: ContactMessageCreate = Body(...),
):
    """Handle contact form submissions with enhanced logging"""
    
    logger.info("="*80)
    logger.info(f"📨 CONTACT FORM SUBMISSION from {payload.email}")
    logger.info(f"Environment: {'RENDER' if os.getenv('RENDER') else 'LOCAL'}")
    logger.info("="*80)
    
    # 1. Save to database
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
        logger.info(f"✅ Database save successful (id: {row.id})")
    except Exception as e:
        logger.error(f"❌ Database save failed: {type(e).__name__}: {str(e)}")
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to save message"}
        )

    # 2. Check email configuration
    logger.info(f"Email Configuration Check:")
    logger.info(f"  SMTP_HOST: {settings.SMTP_HOST}")
    logger.info(f"  SMTP_PORT: {settings.SMTP_PORT}")
    logger.info(f"  SMTP_USER: {settings.SMTP_USER or 'NOT SET'}")
    logger.info(f"  SMTP_PASS: {'SET (len=' + str(len(settings.SMTP_PASS)) + ')' if settings.SMTP_PASS else 'NOT SET'}")
    logger.info(f"  SENDGRID_API_KEY: {'SET' if settings.SENDGRID_API_KEY else 'NOT SET'}")
    logger.info(f"  EMAIL_FROM: {settings.EMAIL_FROM}")
    
    if not settings.SMTP_USER or not settings.SMTP_PASS:
        logger.error("❌ SMTP CREDENTIALS MISSING - Cannot send emails")
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Thank you for reaching out! We've received your message.",
                "details": {
                    "saved_to_database": True,
                    "admin_notified": False,
                    "acknowledgment_sent": False,
                    "reason": "SMTP credentials not configured"
                }
            }
        )
    
    # 3. Initialize email service
    logger.info("Initializing EmailService...")
    email_service = EmailService()
    
    # 4. Determine notification recipient
    notify_to = (
        (settings.CONTACT_MESSAGE_NOTIFY_EMAIL or "").strip() 
        or (settings.SUPERADMIN_EMAIL or "").strip() 
        or settings.EMAIL_FROM
    )
    logger.info(f"Notification will be sent to: {notify_to}")
    
    # 5. Send admin notification
    email_sent = False
    try:
        logger.info(f"📧 Attempting to send admin notification...")
        
        admin_html = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2>🔔 New Contact Message</h2>
            <p><strong>From:</strong> {payload.full_name} ({payload.email})</p>
            <p><strong>Hospital:</strong> {payload.hospital_name or 'N/A'}</p>
            <p><strong>Phone:</strong> {payload.phone or 'N/A'}</p>
            <p><strong>Message:</strong></p>
            <p style="background: #f5f5f5; padding: 15px; border-radius: 5px;">{payload.message}</p>
        </body>
        </html>
        """
        
        admin_text = f"New contact from {payload.full_name} ({payload.email}): {payload.message}"
        
        email_sent = await send_email_safe(
            email_service,
            notify_to,
            f"🔔 [Contact Form] {payload.full_name}",
            admin_html,
            admin_text,
            timeout=15
        )
        
        if email_sent:
            logger.info(f"✅ Admin notification sent successfully to {notify_to}")
        else:
            logger.error(f"❌ Admin notification FAILED to {notify_to}")
            
    except Exception as e:
        logger.error(f"❌ Admin email exception: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    # 6. Send acknowledgment
    ack_sent = False
    if settings.CONTACT_MESSAGE_SEND_ACK:
        try:
            logger.info(f"📧 Attempting to send acknowledgment to {payload.email}...")
            
            ack_html = f"""
            <!DOCTYPE html>
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2>✅ Thank You!</h2>
                <p>Hi {payload.full_name},</p>
                <p>Thank you for reaching out! We received your message and will get back to you soon.</p>
            </body>
            </html>
            """
            
            ack_text = f"Hi {payload.full_name},\n\nThank you for reaching out! We received your message."
            
            ack_sent = await send_email_safe(
                email_service,
                str(payload.email),
                "✅ We Received Your Message",
                ack_html,
                ack_text,
                timeout=15
            )
            
            if ack_sent:
                logger.info(f"✅ Acknowledgment sent successfully to {payload.email}")
            else:
                logger.error(f"❌ Acknowledgment FAILED to {payload.email}")
                
        except Exception as e:
            logger.error(f"❌ Acknowledgment email exception: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
    
    logger.info("="*80)
    logger.info(f"FINAL RESULTS:")
    logger.info(f"  Database saved: ✅")
    logger.info(f"  Admin notified: {'✅' if email_sent else '❌'}")
    logger.info(f"  Acknowledgment sent: {'✅' if ack_sent else '❌'}")
    logger.info("="*80)
    
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": "Thank you for reaching out! We've received your message and will get back to you soon.",
            "details": {
                "saved_to_database": True,
                "admin_notified": email_sent,
                "acknowledgment_sent": ack_sent,
            }
        }
    )
    


@router.get("/render-diagnostics", include_in_schema=True)
async def render_diagnostics():
    """Diagnostic endpoint to debug Render email issues"""
    import os
    
    diagnostics = {
        "environment": {
            "is_render": os.getenv("RENDER", "").lower() in {"true", "1"},
            "render_var": os.getenv("RENDER"),
        },
        "smtp_config": {
            "host": settings.SMTP_HOST,
            "port": settings.SMTP_PORT,
            "user": settings.SMTP_USER,
            "pass_set": bool(settings.SMTP_PASS),
            "pass_length": len(settings.SMTP_PASS) if settings.SMTP_PASS else 0,
            "pass_preview": settings.SMTP_PASS[:20] + "..." if settings.SMTP_PASS else None,
        },
        "sendgrid": {
            "api_key_set": bool(settings.SENDGRID_API_KEY),
            "api_key_length": len(settings.SENDGRID_API_KEY) if settings.SENDGRID_API_KEY else 0,
            "api_key_valid_format": settings.SENDGRID_API_KEY.startswith("SG.") if settings.SENDGRID_API_KEY else False,
        },
        "email_config": {
            "from": settings.EMAIL_FROM,
            "notify_to": settings.CONTACT_MESSAGE_NOTIFY_EMAIL or settings.SUPERADMIN_EMAIL,
            "send_ack": settings.CONTACT_MESSAGE_SEND_ACK,
        },
        "env_vars_raw": {
            "SMTP_HOST": os.getenv("SMTP_HOST"),
            "SMTP_PORT": os.getenv("SMTP_PORT"),
            "SMTP_USER": os.getenv("SMTP_USER"),
            "SMTP_PASS_SET": bool(os.getenv("SMTP_PASS")),
            "SENDGRID_API_KEY_SET": bool(os.getenv("SENDGRID_API_KEY")),
        },
        "test_smtp_connection": None,
    }
    
    # Test multiple connection methods
    port = settings.SMTP_PORT
    connection_methods = []
    
    # Method 1: Plain connection with STARTTLS
    try:
        import aiosmtplib
        
        method = {
            "name": "STARTTLS",
            "port": port,
            "use_tls": False,
            "start_tls": True,
        }
        
        smtp = aiosmtplib.SMTP(hostname=settings.SMTP_HOST, port=port, timeout=10)
        await smtp.connect()
        method["connect"] = "✅"
        
        try:
            await smtp.starttls()
            method["starttls"] = "✅"
        except Exception as e:
            method["starttls"] = f"❌ {str(e)}"
        
        try:
            if settings.SMTP_USER and settings.SMTP_PASS:
                await smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
                method["login"] = "✅"
                method["status"] = "✅ WORKS"
        except Exception as e:
            method["login"] = f"❌ {str(e)}"
            method["status"] = "❌ FAILED"
        
        await smtp.quit()
        connection_methods.append(method)
    except Exception as e:
        connection_methods.append({
            "name": "STARTTLS",
            "status": "❌ FAILED",
            "error": str(e)
        })
    
    # Method 2: Implicit TLS connection
    try:
        method = {
            "name": "Implicit TLS",
            "port": port,
            "use_tls": True,
            "start_tls": False,
        }
        
        smtp = aiosmtplib.SMTP(
            hostname=settings.SMTP_HOST,
            port=port,
            use_tls=True,
            timeout=10
        )
        await smtp.connect()
        method["connect"] = "✅"
        method["starttls"] = "⏭️ SKIPPED"
        
        try:
            if settings.SMTP_USER and settings.SMTP_PASS:
                await smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
                method["login"] = "✅"
                method["status"] = "✅ WORKS"
        except Exception as e:
            method["login"] = f"❌ {str(e)}"
            method["status"] = "❌ FAILED"
        
        await smtp.quit()
        connection_methods.append(method)
    except Exception as e:
        connection_methods.append({
            "name": "Implicit TLS",
            "status": "❌ FAILED",
            "error": str(e)
        })
    
    # Method 3: Plain connection without TLS (for testing)
    try:
        method = {
            "name": "Plain (no TLS)",
            "port": port,
            "use_tls": False,
            "start_tls": False,
        }
        
        smtp = aiosmtplib.SMTP(hostname=settings.SMTP_HOST, port=port, timeout=10)
        await smtp.connect()
        method["connect"] = "✅"
        method["starttls"] = "⏭️ SKIPPED"
        
        try:
            if settings.SMTP_USER and settings.SMTP_PASS:
                await smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
                method["login"] = "✅"
                method["status"] = "✅ WORKS"
        except Exception as e:
            method["login"] = f"❌ {str(e)}"
            method["status"] = "❌ FAILED"
        
        await smtp.quit()
        connection_methods.append(method)
    except Exception as e:
        connection_methods.append({
            "name": "Plain (no TLS)",
            "status": "❌ FAILED",
            "error": str(e)
        })
    
    # Find working method
    working_method = next((m for m in connection_methods if m.get("status") == "✅ WORKS"), None)
    
    diagnostics["test_smtp_connection"] = {
        "port": port,
        "methods_tested": connection_methods,
        "recommended_method": working_method["name"] if working_method else "NONE WORKING",
        "status": "✅ FULLY WORKING" if working_method else "❌ ALL METHODS FAILED"
    }
    
    return diagnostics



@router.get("/env-check", include_in_schema=True)
async def check_environment_variables():
    """Check if environment variables are actually set in Render"""
    import os
    
    required_vars = [
        "SMTP_HOST",
        "SMTP_PORT", 
        "SMTP_USER",
        "SMTP_PASS",
        "SENDGRID_API_KEY",
        "EMAIL_FROM",
    ]
    
    status = {}
    for var in required_vars:
        value = os.getenv(var)
        status[var] = {
            "set": value is not None,
            "empty": value == "" if value is not None else None,
            "length": len(value) if value else 0,
            "preview": value[:20] + "..." if value and len(value) > 20 else value[:50] if value else None
        }
    
    return {
        "environment": "RENDER" if os.getenv("RENDER") else "LOCAL",
        "variables": status,
        "all_configured": all(os.getenv(v) for v in required_vars)
    }