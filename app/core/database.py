"""
Database configuration and session management.
Handles async database connections and Alembic migrations.
Single source of truth: engine and session come from app.database.session
so all routes (deps + direct imports) use the same connection pool.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import logging
from alembic.config import Config
from alembic import command
import os
import threading

from app.core.config import settings

# Single engine and session factory - imported from app.database.session
from app.database.session import (
    get_async_engine,
    AsyncSessionLocal,
    get_db_session,
    get_platform_db_session,
    get_tenant_session_factory,
    resolve_tenant_database_name_for_hospital,
    invalidate_hospital_tenant_cache,
)

logger = logging.getLogger(__name__)

# Migration lock to prevent double-runs during uvicorn reload
_migration_lock = threading.Lock()
_migrations_completed = False


def run_alembic_upgrade():
    """Run Alembic upgrade to head synchronously with reload protection."""
    global _migrations_completed
    
    # Protect against uvicorn reload double-run
    with _migration_lock:
        if _migrations_completed:
            logger.info("Migrations already completed in this process")
            return
        
        try:
            # Get the directory containing alembic.ini
            alembic_cfg_path = os.path.join(os.getcwd(), "alembic.ini")
            
            if not os.path.exists(alembic_cfg_path):
                raise FileNotFoundError(f"alembic.ini not found at {alembic_cfg_path}")
            
            # Create Alembic config
            alembic_cfg = Config(alembic_cfg_path)
            
            # Override the database URL in the config
            alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)
            
            # Run upgrade
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic upgrade completed successfully")
            _migrations_completed = True
            
        except Exception as e:
            logger.error(f"Alembic upgrade failed: {e}")
            raise


async def test_database_connection():
    """Test database connectivity."""
    try:
        engine = get_async_engine()
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.fetchone()
        logger.info("Database connection test successful")
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False


async def init_database():
    """Initialize database with migrations."""
    try:
        # Test connection first
        if not await test_database_connection():
            raise Exception("Database connection failed")
        
        # Run Alembic migrations synchronously (no await needed)
        logger.info("Running database migrations...")
        run_alembic_upgrade()
        
        logger.info("Database initialization completed successfully")
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


async def close_database():
    """Close database connections."""
    engine = get_async_engine()
    await engine.dispose()
    logger.info("Database connections closed")