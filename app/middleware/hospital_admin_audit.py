"""
Automatic audit trail for Hospital Admin API routes.

Writes to `audit_logs` (AuditLog) on the platform database after each successful
response, scoped by hospital_id from the JWT / tenant middleware.
"""
import logging
import uuid
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.enums import UserRole
from app.utils.hospital_admin_audit_labels import resource_label_from_path

logger = logging.getLogger(__name__)

HOSPITAL_ADMIN_PREFIX = "/api/v1/hospital-admin"
# Avoid logging the audit list endpoint on every poll (optional noise reduction).
_SKIP_PATH_SUFFIXES = ("/audit-logs",)


def _http_action(method: str) -> str:
    """Persist enum-compatible strings (must match app.utils.hospital_admin_audit_labels)."""
    m = (method or "").upper()
    if m == "GET":
        return "VIEW"
    if m == "POST":
        return "CREATE"
    if m in ("PUT", "PATCH"):
        return "UPDATE"
    if m == "DELETE":
        return "DELETE"
    return "VIEW"


class HospitalAdminAuditMiddleware(BaseHTTPMiddleware):
    """
    After the request completes, persist an AuditLog row for Hospital Admin traffic.
    Failures are logged and never block the response.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            await self._maybe_log(request, response.status_code)
        except Exception as e:
            logger.warning("Hospital admin audit log failed: %s", e)
        return response

    async def _maybe_log(self, request: Request, status_code: int) -> None:
        path = request.url.path or ""
        if not path.startswith(HOSPITAL_ADMIN_PREFIX):
            return
        if request.method.upper() == "OPTIONS":
            return
        if any(path.rstrip("/").endswith(s.rstrip("/")) for s in _SKIP_PATH_SUFFIXES):
            return
        # Successful responses only (keeps the trail focused on completed actions)
        if not (200 <= status_code < 300):
            return

        user_id = getattr(request.state, "user_id", None)
        hospital_id = getattr(request.state, "hospital_id", None)
        roles = getattr(request.state, "user_roles", None) or []
        if not user_id or not hospital_id:
            return
        if UserRole.HOSPITAL_ADMIN.value not in roles:
            return

        action = _http_action(request.method)
        ip = request.client.host if request.client else None
        ua = (request.headers.get("User-Agent") or "")[:500]
        qs = str(request.query_params)
        if len(qs) > 400:
            qs = qs[:400] + "…"

        resource = resource_label_from_path(path)
        new_values: dict[str, Any] = {
            "path": path[:500],
            "method": request.method.upper(),
            "status_code": status_code,
            "query": qs,
            "resource": resource,
        }

        description = f"{request.method.upper()} {path}"[:2000]
        is_sensitive = request.method.upper() != "GET"

        await self._insert_audit_log(
            user_id=user_id,
            hospital_id=hospital_id,
            action=action,
            description=description,
            new_values=new_values,
            ip_address=ip,
            user_agent=ua,
            is_sensitive=is_sensitive,
        )

    async def _insert_audit_log(
        self,
        *,
        user_id: uuid.UUID,
        hospital_id: uuid.UUID,
        action: str,
        description: str,
        new_values: dict[str, Any],
        ip_address: Optional[str],
        user_agent: Optional[str],
        is_sensitive: bool,
    ) -> None:
        from app.database.session import AsyncSessionLocal
        from app.models.user import AuditLog

        async with AsyncSessionLocal() as db:
            row = AuditLog(
                user_id=user_id,
                hospital_id=hospital_id,
                action=action,
                resource_type="HospitalAdmin",
                resource_id=None,
                description=description,
                old_values=None,
                new_values=new_values,
                ip_address=ip_address,
                user_agent=user_agent,
                session_id=None,
                is_sensitive=is_sensitive,
            )
            db.add(row)
            await db.commit()

