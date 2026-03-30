# app/services/email_service.py

"""
Email service using SendGrid SMTP - Render-optimized
"""
import aiosmtplib
import asyncio
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from typing import Optional
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails via SendGrid SMTP"""
    
    SENDGRID_PORTS = [2525, 587, 465]
    
    def __init__(self):
        self.smtp_host = settings.SMTP_HOST
        self.smtp_port = settings.SMTP_PORT
        self.smtp_user = settings.SMTP_USER
        self.smtp_pass = settings.SMTP_PASS
        self.email_from = settings.EMAIL_FROM
        
        if not self.smtp_user or not self.smtp_pass:
            logger.warning("⚠️  SMTP credentials not configured")
        
        logger.info(
            f"EmailService initialized:\n"
            f"  Host: {self.smtp_host}:{self.smtp_port}\n"
            f"  User: {self.smtp_user}\n"
            f"  Configured: {bool(self.smtp_user and self.smtp_pass)}"
        )
    
    def _get_tls_settings(self, port: int) -> dict:
        """Get TLS settings for each port (tested on Render)"""
        if port == 2525:
            # Port 2525: Opportunistic TLS (auto-upgrade, works on Render)
            return {"use_tls": False, "start_tls": False}
        elif port == 465:
            # Port 465: Implicit SSL/TLS
            return {"use_tls": True, "start_tls": False}
        else:
            # Port 587: STARTTLS
            return {"use_tls": False, "start_tls": True}
    
    async def _try_send_with_port(
        self,
        message,
        port: int,
        timeout: int = 10
    ) -> tuple[bool, str]:
        """Try sending email with specific port"""
        try:
            tls_settings = self._get_tls_settings(port)
            
            logger.info(f"📡 Trying port {port}...")
            
            await asyncio.wait_for(
                aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=port,
                    use_tls=tls_settings["use_tls"],
                    start_tls=tls_settings["start_tls"],
                    username=self.smtp_user,
                    password=self.smtp_pass,
                    timeout=timeout,
                ),
                timeout=timeout + 5
            )
            
            logger.info(f"✅ Email sent via port {port}")
            return True, None
            
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)}"
            logger.warning(f"❌ Port {port} failed: {error}")
            return False, error
    
    async def send_email(
        self, 
        to_email: str, 
        subject: str, 
        html_content: str, 
        text_content: Optional[str] = None,
        timeout: int = 15
    ) -> bool:
        """Send email using SendGrid SMTP"""
        try:
            logger.info(f"📧 Sending to {to_email}: {subject}")
            
            if not self.smtp_user or not self.smtp_pass:
                logger.error("❌ SMTP credentials missing")
                return False
            
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = self.email_from
            message["To"] = to_email
            
            if text_content:
                message.attach(MIMEText(text_content, "plain"))
            message.attach(MIMEText(html_content, "html"))
            
            # Try primary port
            success, error = await self._try_send_with_port(message, self.smtp_port, timeout)
            if success:
                return True
            
            # Try fallback ports
            logger.warning(f"⚠️  Port {self.smtp_port} failed, trying alternatives...")
            for alt_port in self.SENDGRID_PORTS:
                if alt_port == self.smtp_port:
                    continue
                success, _ = await self._try_send_with_port(message, alt_port, timeout)
                if success:
                    logger.info(f"✅ Sent via fallback port {alt_port}")
                    return True
            
            logger.error(f"❌ All ports failed for {to_email}")
            return False
            
        except Exception as e:
            logger.error(f"❌ Send failed: {e}")
            return False

    async def send_document_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        pdf_bytes: bytes,
        filename: str = "document.pdf",
        text_fallback: Optional[str] = None,
        timeout: int = 30
    ) -> bool:
        """Send email with PDF attachment"""
        try:
            logger.info(f"📎 Sending document to {to_email}: {filename}")
            
            if not self.smtp_user or not self.smtp_pass:
                logger.error("❌ SMTP credentials missing")
                return False
            
            message = MIMEMultipart()
            message["Subject"] = subject
            message["From"] = self.email_from
            message["To"] = to_email
            
            if text_fallback:
                message.attach(MIMEText(text_fallback, "plain"))
            message.attach(MIMEText(body_html, "html"))
            
            pdf_attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
            pdf_attachment.add_header("Content-Disposition", "attachment", filename=filename)
            message.attach(pdf_attachment)
            
            tls_settings = self._get_tls_settings(self.smtp_port)
            
            await asyncio.wait_for(
                aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    use_tls=tls_settings["use_tls"],
                    start_tls=tls_settings["start_tls"],
                    username=self.smtp_user,
                    password=self.smtp_pass,
                    timeout=timeout,
                ),
                timeout=timeout + 5
            )
            
            logger.info(f"✅ Document sent to {to_email}")
            return True
                
        except Exception as e:
            logger.error(f"❌ Document send failed: {e}")
            return False

    async def send_verification_email(self, email: str, otp_code: str, first_name: str):
        """Send email verification OTP"""
        html = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0;">🏥 Hospital Management</h1>
            </div>
            <div style="background: #f8f9fa; padding: 40px 30px; border-radius: 0 0 10px 10px;">
                <h2 style="color: #2c3e50; margin-top: 0;">Email Verification</h2>
                <p>Hi <strong>{first_name}</strong>,</p>
                <p>Your verification code:</p>
                <div style="background: white; padding: 25px; border-radius: 8px; text-align: center; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <div style="font-size: 36px; font-weight: bold; color: #667eea; letter-spacing: 8px; font-family: monospace;">{otp_code}</div>
                </div>
                <p style="color: #666;">Expires in 10 minutes.</p>
                <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">
                <p>Best regards,<br><strong>Hospital Management Team</strong></p>
            </div>
        </body>
        </html>
        """
        text = f"Hi {first_name},\n\nYour verification code: {otp_code}\n\nExpires in 10 minutes.\n\nBest regards,\nHospital Management Team"
        return await self.send_email(email, "Verify Your Email - Hospital Management", html, text)
    
    async def send_password_reset_email(self, email: str, otp_code: str, first_name: str):
        """Send password reset OTP"""
        html = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0;">🔐 Password Reset</h1>
            </div>
            <div style="background: #f8f9fa; padding: 40px 30px; border-radius: 0 0 10px 10px;">
                <h2 style="color: #e74c3c; margin-top: 0;">Password Reset Request</h2>
                <p>Hi <strong>{first_name}</strong>,</p>
                <p>Your reset code:</p>
                <div style="background: white; padding: 25px; border-radius: 8px; text-align: center; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <div style="font-size: 36px; font-weight: bold; color: #e74c3c; letter-spacing: 8px; font-family: monospace;">{otp_code}</div>
                </div>
                <p style="color: #666;">Expires in 10 minutes.</p>
                <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">
                <p>Best regards,<br><strong>Hospital Management Team</strong></p>
            </div>
        </body>
        </html>
        """
        text = f"Hi {first_name},\n\nYour password reset code: {otp_code}\n\nExpires in 10 minutes.\n\nBest regards,\nHospital Management Team"
        return await self.send_email(email, "Password Reset - Hospital Management", html, text)