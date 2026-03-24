"""
Security utilities for authentication and authorization.
Handles JWT tokens, password hashing, and permission checking.
"""
from datetime import datetime, timedelta
from typing import Optional, List
from passlib.context import CryptContext
import logging
from jose import JWTError, jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db_session
from app.models.user import User, Role, Permission
from app.core.enums import UserStatus

# Password hashing
logger = logging.getLogger(__name__)
pwd_context = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated="auto")

# JWT token scheme
security = HTTPBearer()


class SecurityManager:
    """Handles authentication and authorization"""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash password.
        Prefer bcrypt; if backend is unavailable/incompatible in runtime,
        gracefully fallback to pbkdf2_sha256 so startup/login don't break.
        """
        try:
            return pwd_context.hash(password, scheme="bcrypt")
        except Exception as e:
            logger.warning(f"bcrypt hashing unavailable, using pbkdf2_sha256 fallback: {e}")
            return pwd_context.hash(password, scheme="pbkdf2_sha256")
    
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception as e:
            logger.warning(f"Password verify failed due to hash backend error: {e}")
            return False
    
    @staticmethod
    def generate_temp_password(length: int = 12) -> str:
        """Generate a temporary password"""
        import secrets
        import string
        
        # Define character sets
        lowercase = string.ascii_lowercase
        uppercase = string.ascii_uppercase
        digits = string.digits
        special = "!@#$%^&*"
        
        # Ensure at least one character from each set
        password = [
            secrets.choice(lowercase),
            secrets.choice(uppercase),
            secrets.choice(digits),
            secrets.choice(special)
        ]
        
        # Fill the rest with random characters
        all_chars = lowercase + uppercase + digits + special
        for _ in range(length - 4):
            password.append(secrets.choice(all_chars))
        
        # Shuffle the password
        secrets.SystemRandom().shuffle(password)
        
        return ''.join(password)
    
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT access token"""
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire, "type": "access"})
        return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    
    @staticmethod
    def create_refresh_token(data: dict) -> str:
        """Create JWT refresh token"""
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        to_encode.update({"exp": expire, "type": "refresh"})
        return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    
    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> dict:
        """Verify and decode JWT token"""
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            
            # Check token type
            if payload.get("type") != token_type:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type"
                )
            
            # Check expiration
            exp = payload.get("exp")
            if exp and datetime.utcnow().timestamp() > exp:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired"
                )
            
            return payload
            
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session)
) -> User:
    """
    Get current authenticated user from JWT token.
    
    Usage:
        @app.get("/protected")
        async def protected_route(current_user: User = Depends(get_current_user)):
            return {"user_id": current_user.id}
    """
    # Verify token
    payload = SecurityManager.verify_token(credentials.credentials)
    
    # Extract user information
    user_id = payload.get("sub")  # Use "sub" as per JWT standard
    hospital_id = payload.get("hospital_id")
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )
    
    # Get user from database
    if hospital_id:
        result = await db.execute(
            select(User)
            .where(User.id == user_id, User.hospital_id == hospital_id)
            .options(selectinload(User.roles).selectinload(Role.permissions))
        )
    else:
        # For Super Admin or users without hospital_id
        result = await db.execute(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.roles).selectinload(Role.permissions))
        )
    
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    # Check user status
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is not active"
        )
    
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Get current active user (additional validation)"""
    if current_user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user


def require_permissions(required_permissions: List[str]):
    """
    Decorator to require specific permissions for endpoint access.
    
    Usage:
        @app.get("/admin-only")
        @require_permissions(["user.create", "user.delete"])
        async def admin_endpoint(current_user: User = Depends(get_current_user)):
            return {"message": "Admin access granted"}
    """
    def permission_checker(current_user: User = Depends(get_current_user)):
        # Get user permissions
        user_permissions = []
        for role in current_user.roles:
            for permission in role.permissions:
                user_permissions.append(permission.name)
        
        # Check if user has required permissions
        missing_permissions = set(required_permissions) - set(user_permissions)
        if missing_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(missing_permissions)}"
            )
        
        return current_user
    
    return permission_checker


def require_roles(required_roles: List[str]):
    """
    Decorator to require specific roles for endpoint access.
    
    Usage:
        @app.get("/doctor-only")
        @require_roles(["DOCTOR", "HOSPITAL_ADMIN"])
        async def doctor_endpoint(current_user: User = Depends(get_current_user)):
            return {"message": "Doctor access granted"}
    """
    def role_checker(current_user: User = Depends(get_current_user)):
        # Get user roles
        user_roles = [role.name for role in current_user.roles]
        
        # Check if user has required roles
        if not any(role in user_roles for role in required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required roles: {', '.join(required_roles)}"
            )
        
        return current_user
    
    return role_checker


async def check_hospital_access(user: User, resource_hospital_id: int) -> bool:
    """
    Check if user has access to resources from specific hospital.
    Enforces multi-tenant isolation.
    """
    return user.hospital_id == resource_hospital_id


def get_user_permissions(user: User) -> List[str]:
    """Get all permissions for a user"""
    permissions = []
    for role in user.roles:
        for permission in role.permissions:
            permissions.append(permission.name)
    return list(set(permissions))  # Remove duplicates


def get_user_roles(user: User) -> List[str]:
    """Get all roles for a user"""
    return [role.name for role in user.roles]