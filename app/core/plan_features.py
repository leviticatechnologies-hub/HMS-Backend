"""
Subscription plan → module feature flags.

Keys are stable for API + frontend (Dashboard / module toggles).
Plans align with `SubscriptionPlan` enum names on `subscription_plans.name`.
"""
from __future__ import annotations

from typing import Dict, FrozenSet

# Canonical feature keys (use everywhere: DB JSON overrides, APIs, UI)
FEATURE_LAB_TESTS = "lab_tests"
FEATURE_VIDEO_CONSULTATION = "video_consultation"
FEATURE_PHARMACY = "pharmacy"

ALL_FEATURE_KEYS: FrozenSet[str] = frozenset(
    {FEATURE_LAB_TESTS, FEATURE_VIDEO_CONSULTATION, FEATURE_PHARMACY}
)

# Defaults when `subscription_plans.features` JSON has no override for a key.
# BASIC tier ≈ STANDARD: Lab on, Video + Pharmacy off. PREMIUM: all on. FREE: conservative.
DEFAULT_FEATURES_BY_PLAN: Dict[str, Dict[str, bool]] = {
    "FREE": {
        FEATURE_LAB_TESTS: False,
        FEATURE_VIDEO_CONSULTATION: False,
        FEATURE_PHARMACY: False,
    },
    "STANDARD": {
        FEATURE_LAB_TESTS: True,
        FEATURE_VIDEO_CONSULTATION: False,
        FEATURE_PHARMACY: False,
    },
    "PREMIUM": {
        FEATURE_LAB_TESTS: True,
        FEATURE_VIDEO_CONSULTATION: True,
        FEATURE_PHARMACY: True,
    },
}

# Alias names from DB / UI → plan bucket used for defaults
PLAN_NAME_ALIASES: Dict[str, str] = {
    "BASIC": "STANDARD",
    "PRO": "PREMIUM",
}


def normalize_plan_name(name: str | None) -> str:
    if not name:
        return "STANDARD"
    u = name.strip().upper()
    return PLAN_NAME_ALIASES.get(u, u)
