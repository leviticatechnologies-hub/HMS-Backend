"""
Application configuration settings.
Manages environment variables and application settings.
"""
from pydantic_settings import BaseSettings
from pydantic import Field, model_validator, field_validator
from typing import Optional
import os
import logging
from urllib.parse import urlparse
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Force load .env for local development
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"

if os.getenv("RENDER", "").lower() not in {"true", "1"}:
    if ENV_FILE.exists():
        load_dotenv(dotenv_path=ENV_FILE, override=True)
        logger.info(f"✓ Loaded .env from {ENV_FILE}")
    else:
        logger.warning(f"✗ .env not found at {ENV_FILE}")
else:
    logger.info("Running in Render - using environment variables")


class Settings(BaseSettings):
    """Application settings with environment variable support"""
    
    # Application
    APP_NAME: str = "Hospital Management SaaS"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = Field(default=False, env="DEBUG")
    OPENAPI_DOCS: bool = Field(default=True, env="OPENAPI_DOCS")
    
    # Database Configuration
    DB_HOST: str = Field(default="localhost", env="DB_HOST")
    DB_PORT: int = Field(default=5432, env="DB_PORT")
    DB_USER: str = Field(default="abc", env="DB_USER")
    DB_PASSWORD: str = Field(default="abc", env="DB_PASSWORD")
    DB_NAME: str = Field(default="abc", env="DB_NAME")
    
    # Database Pool Configuration
    DB_POOL_SIZE: int = Field(default=5, env="DB_POOL_SIZE")
    DB_MAX_OVERFLOW: int = Field(default=10, env="DB_MAX_OVERFLOW")
    
    # Database URLs - Direct from environment
    DATABASE_URL: str = Field(default="", env="DATABASE_URL")
    DATABASE_URL_SYNC: str = Field(default="", env="DATABASE_URL_SYNC")
    DB_PRUNE_UNUSED_TABLES: bool = Field(default=False, env="DB_PRUNE_UNUSED_TABLES")
    DB_BOOTSTRAP_FROM_MODELS: bool = Field(default=True, env="DB_BOOTSTRAP_FROM_MODELS")

    # Redis / Caching
    REDIS_URL: str = Field(default="redis://localhost:6379/0", env="REDIS_URL")
    
    # Super Admin Configuration
    SUPERADMIN_EMAIL: str = Field(default="kiranios456@gmail.com", env="SUPERADMIN_EMAIL")
    SUPERADMIN_PASSWORD: str = Field(default="Admin123", env="SUPERADMIN_PASSWORD")
    SUPERADMIN_FIRST_NAME: str = Field(default="Super", env="SUPERADMIN_FIRST_NAME")
    SUPERADMIN_LAST_NAME: str = Field(default="Admin", env="SUPERADMIN_LAST_NAME")
    
    # Security
    SECRET_KEY: str = Field(default="your-secret-key-change-in-production", env="SECRET_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # CORS
    ALLOWED_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:8080"],
        env="ALLOWED_ORIGINS",
    )

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_allowed_origins(cls, v):
        if v is None:
            return ["http://localhost:3000", "http://localhost:8080"]
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return ["http://localhost:3000", "http://localhost:8080"]
            if raw == "*":
                return ["*"]
            return [item.strip() for item in raw.split(",") if item.strip()]
        return v

    # Public URL of this backend
    APP_PUBLIC_URL: str = Field(default="http://localhost:8060", env="APP_PUBLIC_URL")
    
    # Email Configuration - SendGrid
    SENDGRID_API_KEY: str = Field(default="", env="SENDGRID_API_KEY")
    EMAIL_FROM: str = Field(default="kiranios456@gmail.com", env="EMAIL_FROM")
    
    # Legacy SMTP settings (for backward compatibility)
    SMTP_HOST: str = Field(default="smtp.gmail.com", env="SMTP_HOST")
    SMTP_PORT: int = Field(default=2525, env="SMTP_PORT")
    SMTP_USER: str = Field(default="", env="SMTP_USER")
    SMTP_PASS: str = Field(default="", env="SMTP_PASS")
    
    # File Upload
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    UPLOAD_DIR: str = "uploads"
    ALLOWED_FILE_TYPES: list = [".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"]
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    
    # Logging
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    
    # Hospital Information
    HOSPITAL_NAME: str = Field(default="City General Hospital", env="HOSPITAL_NAME")
    HOSPITAL_ADDRESS: str = Field(default="123 Medical Center Drive", env="HOSPITAL_ADDRESS")
    HOSPITAL_PHONE: str = Field(default="(555) 123-4567", env="HOSPITAL_PHONE")
    HOSPITAL_EMAIL: str = Field(default="billing@hospital.com", env="HOSPITAL_EMAIL")
    
    # PDF Storage
    PDF_STORAGE_PATH: str = Field(default="./pdfs", env="PDF_STORAGE_PATH")
    
    # SMS Configuration (Twilio)
    TWILIO_ACCOUNT_SID: str = Field(default="", env="TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: str = Field(default="", env="TWILIO_AUTH_TOKEN")
    TWILIO_FROM_NUMBER: str = Field(default="", env="TWILIO_FROM_NUMBER")
    
    # Payment Gateways
    STRIPE_SECRET_KEY: str = Field(default="", env="STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET: str = Field(default="", env="STRIPE_WEBHOOK_SECRET")
    STRIPE_PUBLISHABLE_KEY: str = Field(default="", env="STRIPE_PUBLISHABLE_KEY")
    
    RAZORPAY_KEY_ID: str = Field(default="", env="RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET: str = Field(default="", env="RAZORPAY_KEY_SECRET")
    RAZORPAY_WEBHOOK_SECRET: str = Field(default="", env="RAZORPAY_WEBHOOK_SECRET")
    
    PAYTM_MID: str = Field(default="", env="PAYTM_MID")
    PAYTM_KEY: str = Field(default="", env="PAYTM_KEY")
    PAYTM_ENV: str = Field(default="sandbox", env="PAYTM_ENV")
    PAYTM_WEBSITE: str = Field(default="WEBSTAGING", env="PAYTM_WEBSITE")
    PAYTM_CALLBACK_URL: str = Field(default="", env="PAYTM_CALLBACK_URL")

    # Public demo request
    DEMO_REQUEST_NOTIFY_EMAIL: str = Field(default="", env="DEMO_REQUEST_NOTIFY_EMAIL")
    DEMO_REQUEST_SEND_CONFIRMATION: bool = Field(default=True, env="DEMO_REQUEST_SEND_CONFIRMATION")
    CONTACT_MESSAGE_NOTIFY_EMAIL: str = Field(default="", env="CONTACT_MESSAGE_NOTIFY_EMAIL")
    CONTACT_MESSAGE_SEND_ACK: bool = Field(default=True, env="CONTACT_MESSAGE_SEND_ACK")

    @model_validator(mode="after")
    def normalize_database_urls(self):
        """Normalize database URLs"""
        async_url = (self.DATABASE_URL or "").strip()
        sync_url = (self.DATABASE_URL_SYNC or "").strip()

        if not async_url and not sync_url:
            raise ValueError("Set DATABASE_URL and/or DATABASE_URL_SYNC in environment variables")

        if async_url and not sync_url:
            sync_url = self._to_sync_url(async_url)
        elif sync_url and not async_url:
            async_url = self._to_async_url(sync_url)
        else:
            async_url = self._to_async_url(async_url)
            sync_url = self._to_sync_url(sync_url)
            if self._is_local_url(async_url) and not self._is_local_url(sync_url):
                async_url = self._to_async_url(sync_url)
            elif self._is_local_url(sync_url) and not self._is_local_url(async_url):
                sync_url = self._to_sync_url(async_url)

        if os.getenv("RENDER", "").lower() in {"true", "1"}:
            if self._is_local_url(async_url) or self._is_local_url(sync_url):
                logger.warning(
                    "Database URL points to localhost in Render. "
                    "Set DATABASE_URL to your Render Postgres URL."
                )

        self.DATABASE_URL = async_url
        self.DATABASE_URL_SYNC = sync_url
        return self

    @staticmethod
    def _to_async_url(url: str) -> str:
        value = (url or "").strip()
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql+psycopg2://"):
            value = value.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql://"):
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @staticmethod
    def _to_sync_url(url: str) -> str:
        value = (url or "").strip()
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql://", 1)
        elif value.startswith("postgresql+asyncpg://"):
            value = value.replace("postgresql+asyncpg://", "postgresql://", 1)
        return value

    @staticmethod
    def _is_local_url(url: str) -> bool:
        value = (url or "").strip()
        if not value:
            return True
        try:
            host = (urlparse(value).hostname or "").lower()
        except Exception:
            return False
        return host in {"localhost", "127.0.0.1", "::1"}
    
    @property
    def database_url(self) -> str:
        return self.DATABASE_URL
    
    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL_SYNC
    
    @property
    def sync_database_url(self) -> str:
        return self.DATABASE_URL_SYNC
    
    def log_config(self) -> None:
        """Log configuration details (mask sensitive data)"""
        import re
        masked_url = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', self.DATABASE_URL)
        masked_sync_url = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', self.DATABASE_URL_SYNC)
        
        logger.info("=" * 60)
        logger.info("Configuration Loaded:")
        logger.info(f"  Database Host: {self.DB_HOST}")
        logger.info(f"  Database Name: {self.DB_NAME}")
        logger.info(f"  Async URL: {masked_url}")
        logger.info(f"  Sync URL: {masked_sync_url}")
        logger.info(f"  Email Provider: SendGrid")
        logger.info(f"  SendGrid API Key: {'SET (' + self.SENDGRID_API_KEY[:20] + '...)' if self.SENDGRID_API_KEY else 'NOT SET ⚠️'}")
        logger.info(f"  Email From: {self.EMAIL_FROM}")
        logger.info(f"  Contact Notify: {self.CONTACT_MESSAGE_NOTIFY_EMAIL or 'Using SUPERADMIN_EMAIL'}")
        logger.info("=" * 60)
    
    class Config:
        # Don't use env_file in Config - we already loaded it above
        case_sensitive = True
        # Allow extra fields for future compatibility
        extra = "ignore"


# Global settings instance
settings = Settings()

# Log configuration on import
settings.log_config()