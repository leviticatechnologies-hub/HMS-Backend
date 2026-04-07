"""Schemas for subscription feature flags (dashboard / modules)."""
from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, Field


class HospitalFeatureFlagsOut(BaseModel):
    plan_name: Optional[str] = Field(None, description="Raw plan name from subscription_plans.name")
    plan_display_name: Optional[str] = None
    features: Dict[str, bool] = Field(
        ...,
        description="lab_tests, video_consultation, pharmacy — for UI module toggles",
    )
