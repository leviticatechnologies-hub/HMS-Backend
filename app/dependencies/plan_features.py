"""
FastAPI dependencies: gate routes by subscription plan feature flags.
Uses platform DB for `subscription_plans` + `hospital_subscriptions`.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_platform_db_session
from app.dependencies.auth import require_hospital_context
from app.services.subscription_feature_service import is_feature_enabled


def require_plan_feature(feature_key: str):
    """
    Block the request with 403 if the hospital's plan does not enable this feature.
    Super Admin bypasses (no hospital subscription gate).
    """

    async def _checker(
        context: Dict[str, Any] = Depends(require_hospital_context),
        db: AsyncSession = Depends(get_platform_db_session),
    ) -> None:
        roles = context.get("roles") or []
        if "SUPER_ADMIN" in roles:
            return
        hid = uuid.UUID(context["hospital_id"])
        ok = await is_feature_enabled(db, hid, feature_key)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FEATURE_NOT_AVAILABLE",
                    "message": "This feature is not enabled for your subscription plan.",
                    "feature": feature_key,
                },
            )

    return _checker
