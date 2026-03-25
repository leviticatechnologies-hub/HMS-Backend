"""
Tenant isolation middleware for multi-tenant hospital management system.
Ensures strict data isolation between hospitals.
"""
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from jose import jwt, JWTError
import uuid
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class TenantIsolationMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce tenant isolation across all API requests.
    
    Features:
    - Extracts hospital_id from JWT token
    - Injects hospital context into request
    - Blocks cross-hospital access attempts
    - Logs security violations
    """
    
    def __init__(self, app):
        super().__init__(app)
        self.public_paths = {
            "/",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/health",
            "/api/v1/auth/login",
            "/api/v1/auth/patient/register",
            "/api/v1/auth/patient/verify-otp",
            "/api/v1/auth/patient/login",
            "/api/v1/auth/patient/forgot-password",
            "/api/v1/auth/patient/reset-password",
            "/api/v1/auth/hospitals",
            "/demo/request",
            "/contact/send",
        }
        
        # Super Admin paths that bypass tenant isolation
        self.super_admin_paths = {
            "/api/v1/super-admin",
            "/api/v1/analytics",
        }
        
        # Patient paths that don't require hospital_id (patients can access multiple hospitals)
        self.patient_paths = {
            "/api/v1/patient-appointment-booking",
            "/api/v1/patient-document-storage",
            "/api/v1/patient-medical-history",
            "/api/v1/patient-discharge-summary",
            "/api/v1/patient/lab-reports"
        }
    
    async def dispatch(self, request: Request, call_next):
        """Process request and enforce tenant isolation"""
        
        # Skip tenant isolation for public endpoints
        if self._is_public_path(request.url.path):
            return await call_next(request)
        
        # Super Admin paths bypass tenant isolation but still require authentication
        if self._is_super_admin_path(request.url.path):
            # Extract user context but don't require hospital_id
            try:
                await self._extract_super_admin_context(request)
            except HTTPException as e:
                return JSONResponse(
                    status_code=e.status_code,
                    content={
                        "error": {
                            "code": "AUTH_REQUIRED",
                            "message": e.detail,
                            "path": request.url.path
                        }
                    }
                )
            return await call_next(request)
        
        # Patient paths don't require hospital_id (patients can access multiple hospitals)
        if self._is_patient_path(request.url.path):
            try:
                await self._extract_patient_context(request)
            except HTTPException as e:
                return JSONResponse(
                    status_code=e.status_code,
                    content={
                        "error": {
                            "code": "AUTH_REQUIRED",
                            "message": e.detail,
                            "path": request.url.path
                        }
                    }
                )
            return await call_next(request)
        
        # Extract and validate hospital context
        try:
            hospital_id = await self._extract_hospital_context(request)
            if hospital_id:
                request.state.hospital_id = hospital_id
                request.state.is_authenticated = True
                logger.debug(f"Request authenticated for hospital: {hospital_id}")
            else:
                request.state.hospital_id = None
                request.state.is_authenticated = False
        
        except HTTPException as e:
            # Return authentication error
            return JSONResponse(
                status_code=e.status_code,
                content={
                    "error": {
                        "code": "AUTH_REQUIRED",
                        "message": e.detail,
                        "path": request.url.path
                    }
                }
            )
        except Exception as e:
            # Let the actual error bubble up instead of masking it
            raise e
        
        # Process request
        response = await call_next(request)
        
        # Add security headers
        response.headers["X-Hospital-Context"] = str(hospital_id) if hospital_id else "none"
        response.headers["X-Tenant-Isolated"] = "true"
        
        return response
    
    def _is_public_path(self, path: str) -> bool:
        """Check if path is public (no authentication required)"""
        # Exact match
        if path in self.public_paths:
            return True
        
        # Pattern matching for API docs
        if path.startswith("/docs") or path.startswith("/redoc"):
            return True
        
        return False
    
    def _is_super_admin_path(self, path: str) -> bool:
        """Check if path is a Super Admin endpoint"""
        for super_admin_path in self.super_admin_paths:
            if path.startswith(super_admin_path):
                return True
        return False
    
    def _is_patient_path(self, path: str) -> bool:
        """Check if path is a Patient endpoint (no hospital_id required)"""
        for patient_path in self.patient_paths:
            if path.startswith(patient_path):
                return True
        return False
    
    async def _extract_super_admin_context(self, request: Request):
        """Extract user context for Super Admin endpoints (no hospital_id required)"""
        # Get Authorization header
        authorization = request.headers.get("Authorization")
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required for Super Admin endpoints"
            )
        
        # Extract token
        try:
            scheme, token = authorization.split()
            if scheme.lower() != "bearer":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication scheme"
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format"
            )
        
        # Decode and validate JWT token
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )
            
            # Store user context (hospital_id is optional for Super Admin)
            user_id_str = payload.get("user_id")
            if user_id_str:
                request.state.user_id = uuid.UUID(user_id_str)
            
            request.state.user_roles = payload.get("roles", [])
            request.state.user_permissions = payload.get("permissions", [])
            request.state.is_authenticated = True
            
            # Hospital ID is optional for Super Admin
            hospital_id_str = payload.get("hospital_id")
            if hospital_id_str:
                request.state.hospital_id = uuid.UUID(hospital_id_str)
            else:
                request.state.hospital_id = None
            
        except JWTError as e:
            logger.warning(f"JWT validation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
        except ValueError as e:
            logger.warning(f"Invalid UUID in token: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid user context in token"
            )
    
    async def _extract_patient_context(self, request: Request):
        """Extract user context for Patient endpoints (no hospital_id required)"""
        # Get Authorization header
        authorization = request.headers.get("Authorization")
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
        
        # Extract token
        try:
            scheme, token = authorization.split()
            if scheme.lower() != "bearer":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication scheme"
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format"
            )
        
        # Decode and validate JWT token
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )
            
            # Store user context (hospital_id is optional for patients)
            user_id_str = payload.get("sub") or payload.get("user_id")
            if user_id_str:
                request.state.user_id = uuid.UUID(user_id_str)
            
            request.state.user_roles = payload.get("roles", [])
            request.state.user_permissions = payload.get("permissions", [])
            request.state.is_authenticated = True
            
            # Hospital ID is optional for patients (they can book at any hospital)
            hospital_id_str = payload.get("hospital_id")
            if hospital_id_str:
                request.state.hospital_id = uuid.UUID(hospital_id_str)
            else:
                request.state.hospital_id = None
            
        except JWTError as e:
            logger.warning(f"JWT validation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
        except ValueError as e:
            logger.warning(f"Invalid UUID in token: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid user context in token"
            )
    
    async def _extract_hospital_context(self, request: Request) -> uuid.UUID:
        """
        Extract hospital_id from JWT token in Authorization header.
        hospital_id is assigned to patients only at registration, so token must include it.
        """
        # Get Authorization header
        authorization = request.headers.get("Authorization")
        if not authorization:
            return None
        
        # Extract token
        try:
            scheme, token = authorization.split()
            if scheme.lower() != "bearer":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication scheme"
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format"
            )
        
        # Decode and validate JWT token
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )
            
            # Store user context
            user_id_str = payload.get("sub") or payload.get("user_id")
            if user_id_str:
                request.state.user_id = uuid.UUID(user_id_str)
            request.state.user_roles = payload.get("roles", [])
            request.state.user_permissions = payload.get("permissions", [])

            hospital_id_str = payload.get("hospital_id")
            if not hospital_id_str:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Hospital context missing from token"
                )
            
            hospital_id = uuid.UUID(hospital_id_str)
            return hospital_id
            
        except JWTError as e:
            logger.warning(f"JWT validation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
        except ValueError as e:
            logger.warning(f"Invalid UUID in token: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid hospital context in token"
            )
    
    def _log_security_violation(self, request: Request, violation_type: str, details: str):
        """Log security violations for audit purposes"""
        logger.warning(
            f"Security violation: {violation_type}",
            extra={
                "violation_type": violation_type,
                "details": details,
                "path": request.url.path,
                "method": request.method,
                "client_ip": request.client.host if request.client else None,
                "user_agent": request.headers.get("User-Agent"),
                "hospital_id": getattr(request.state, "hospital_id", None),
                "user_id": getattr(request.state, "user_id", None)
            }
        )


class HospitalContextValidator:
    """
    Utility class to validate hospital context in API endpoints.
    Use this in endpoints that need to ensure data belongs to the correct hospital.
    """
    
    @staticmethod
    def validate_hospital_access(request: Request, resource_hospital_id: uuid.UUID):
        """
        Validate that the current user can access a resource from a specific hospital.
        
        Args:
            request: FastAPI request object with hospital context
            resource_hospital_id: Hospital ID of the resource being accessed
        
        Raises:
            HTTPException: If cross-hospital access is attempted
        """
        current_hospital_id = getattr(request.state, "hospital_id", None)
        
        if not current_hospital_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "AUTH_REQUIRED",
                    "message": "Authentication required"
                }
            )
        
        if current_hospital_id != resource_hospital_id:
            logger.warning(
                f"Cross-hospital access attempt: user from {current_hospital_id} "
                f"trying to access resource from {resource_hospital_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTH_006",
                    "message": "Cross-hospital access denied"
                }
            )
    
    @staticmethod
    def get_hospital_context(request: Request) -> uuid.UUID:
        """
        Get hospital context from request state.
        
        Args:
            request: FastAPI request object
        
        Returns:
            UUID: Current hospital ID
        
        Raises:
            HTTPException: If no hospital context found
        """
        hospital_id = getattr(request.state, "hospital_id", None)
        
        if not hospital_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "AUTH_REQUIRED",
                    "message": "Hospital context required"
                }
            )
        
        return hospital_id
    
    @staticmethod
    def has_permission(request: Request, required_permission: str) -> bool:
        """
        Check if current user has required permission.
        
        Args:
            request: FastAPI request object
            required_permission: Permission string (e.g., "patient.create")
        
        Returns:
            bool: True if user has permission, False otherwise
        """
        user_permissions = getattr(request.state, "user_permissions", [])
        return required_permission in user_permissions
    
    @staticmethod
    def has_role(request: Request, required_role: str) -> bool:
        """
        Check if current user has required role.
        
        Args:
            request: FastAPI request object
            required_role: Role string (e.g., "DOCTOR")
        
        Returns:
            bool: True if user has role, False otherwise
        """
        user_roles = getattr(request.state, "user_roles", [])
        return required_role in user_roles