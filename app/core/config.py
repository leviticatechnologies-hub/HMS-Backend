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
from dotenv import load_dotenv

# Load .env only for local/dev. In Render, rely on service environment variables.
if os.getenv("RENDER", "").lower() not in {"true", "1"}:
    load_dotenv()

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings with environment variable support"""
    
    # Application
    APP_NAME: str = "Hospital Management SaaS"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = Field(default=False, env="DEBUG")
    # Show OpenAPI docs at /docs and /redoc (set False in production to hide)
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
    # Optional: prune legacy tables not used by current models (dev/local only)
    DB_PRUNE_UNUSED_TABLES: bool = Field(default=False, env="DB_PRUNE_UNUSED_TABLES")
    # Optional: bootstrap schema directly from SQLAlchemy models instead of Alembic
    # When True, main.py will call Base.metadata.create_all() on startup and skip migrations.
    DB_BOOTSTRAP_FROM_MODELS: bool = Field(default=True, env="DB_BOOTSTRAP_FROM_MODELS")

    # Redis / Caching
    REDIS_URL: str = Field(default="redis://localhost:6379/0", env="REDIS_URL")
    
    # Super Admin Configuration
    SUPERADMIN_EMAIL: str = Field(default="superadmin@hsm.com", env="SUPERADMIN_EMAIL")
    SUPERADMIN_PASSWORD: str = Field(default="SuperAdmin123!", env="SUPERADMIN_PASSWORD")
    SUPERADMIN_FIRST_NAME: str = Field(default="Super", env="SUPERADMIN_FIRST_NAME")
    SUPERADMIN_LAST_NAME: str = Field(default="Admin", env="SUPERADMIN_LAST_NAME")
    
    # Security
    SECRET_KEY: str = Field(default="your-secret-key-change-in-production", env="SECRET_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # CORS
    # Accepts either:
    # - comma-separated string via env, e.g. https://a.com,https://b.com
    # - or JSON-ish list parsing supported by Pydantic if provided as a list.
    ALLOWED_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:8080",
            "https://hospital-management-12.vercel.app",
        ],
        env="ALLOWED_ORIGINS",
    )

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_allowed_origins(cls, v):
        if v is None:
            return [
                "http://localhost:3000",
                "http://localhost:3001",
                "http://localhost:8080",
            ]
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return [
                    "http://localhost:3000",
                    "http://localhost:3001",
                    "http://localhost:8080",
                ]
            # Allow user to pass '*' but note: allow_credentials=True in main.py.
            # So do not recommend '*' unless you also set allow_credentials=False.
            if raw == "*":
                return ["*"]
            return [item.strip() for item in raw.split(",") if item.strip()]
        return v

    # Public URL of this backend (used to generate links sent to frontend/emails).
    # Set this to your Render URL, e.g. https://hospital-backend-9mg3.onrender.com
    APP_PUBLIC_URL: str = Field(default="http://localhost:8060", env="APP_PUBLIC_URL")
    
    # Email Configuration
    SMTP_HOST: str = Field(default="smtp.gmail.com", env="SMTP_HOST")
    SMTP_PORT: int = Field(default=587, env="SMTP_PORT")
    SMTP_USER: str = Field(default="cheekatiabhinaya@gmail.com", env="SMTP_USER")
    SMTP_PASS: str = Field(default="wpjeppaqlcnyxbod", env="SMTP_PASS")
    EMAIL_FROM: str = Field(default="cheekatiabhinaya@gmail.com", env="EMAIL_FROM")
    
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
    
    # ========================================
    # BILLING MODULE CONFIGURATION
    # ========================================
    
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
    
    # ========================================
    # PAYMENT GATEWAY CONFIGURATION
    # ========================================
    
    # Stripe
    STRIPE_SECRET_KEY: str = Field(default="", env="STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET: str = Field(default="", env="STRIPE_WEBHOOK_SECRET")
    STRIPE_PUBLISHABLE_KEY: str = Field(default="", env="STRIPE_PUBLISHABLE_KEY")
    
    # Razorpay
    RAZORPAY_KEY_ID: str = Field(default="", env="RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET: str = Field(default="", env="RAZORPAY_KEY_SECRET")
    RAZORPAY_WEBHOOK_SECRET: str = Field(default="", env="RAZORPAY_WEBHOOK_SECRET")
    
    # Paytm
    PAYTM_MID: str = Field(default="", env="PAYTM_MID")
    PAYTM_KEY: str = Field(default="", env="PAYTM_KEY")
    PAYTM_ENV: str = Field(default="sandbox", env="PAYTM_ENV")
    PAYTM_WEBSITE: str = Field(default="WEBSTAGING", env="PAYTM_WEBSITE")
    PAYTM_CALLBACK_URL: str = Field(default="", env="PAYTM_CALLBACK_URL")

    # Public demo request (DCM / marketing form)
    DEMO_REQUEST_NOTIFY_EMAIL: str = Field(default="", env="DEMO_REQUEST_NOTIFY_EMAIL")
    DEMO_REQUEST_SEND_CONFIRMATION: bool = Field(default=True, env="DEMO_REQUEST_SEND_CONFIRMATION")
    CONTACT_MESSAGE_NOTIFY_EMAIL: str = Field(default="", env="CONTACT_MESSAGE_NOTIFY_EMAIL")
    CONTACT_MESSAGE_SEND_ACK: bool = Field(default=True, env="CONTACT_MESSAGE_SEND_ACK")

    @model_validator(mode="after")
    def normalize_database_urls(self):
        """
        Allow Render-style single URL setup by deriving the missing URL.
        Ensures async engine gets asyncpg URL and sync operations get sync URL.
        """
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
            # If one URL points to localhost and the other doesn't, trust the non-localhost URL.
            # This prevents repo .env localhost values from breaking cloud deployments.
            if self._is_local_url(async_url) and not self._is_local_url(sync_url):
                async_url = self._to_async_url(sync_url)
            elif self._is_local_url(sync_url) and not self._is_local_url(async_url):
                sync_url = self._to_sync_url(async_url)

        # Render should never run against localhost DB.
        # Warn instead of raising, so the app can still boot in degraded mode
        # and expose health/docs while env vars are being fixed.
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
    
    # Lowercase alias properties for backward compatibility
    @property
    def database_url(self) -> str:
        """Lowercase alias for DATABASE_URL"""
        return self.DATABASE_URL
    
    @property
    def database_url_sync(self) -> str:
        """Lowercase alias for DATABASE_URL_SYNC"""
        return self.DATABASE_URL_SYNC
    
    @property
    def sync_database_url(self) -> str:
        """Alternative alias for DATABASE_URL_SYNC"""
        return self.DATABASE_URL_SYNC
    
    def log_config(self) -> None:
        """Log configuration details (mask password)"""
        import re
        masked_url = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', self.DATABASE_URL)
        masked_sync_url = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', self.DATABASE_URL_SYNC)
        
        logger.debug(f"Database Config Loaded:")
        logger.debug(f"   Host: {self.DB_HOST}")
        logger.debug(f"   Database: {self.DB_NAME}")
        logger.debug(f"   Async URL: {masked_url}")
        logger.debug(f"   Sync URL: {masked_sync_url}")
    
    class Config:
        # Never read repository .env on Render.
        # This prevents localhost credentials from being used in production.
        env_file = None if os.getenv("RENDER", "").lower() in {"true", "1"} else ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()

# Log configuration on import
settings.log_config()