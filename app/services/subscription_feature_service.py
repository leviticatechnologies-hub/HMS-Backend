"""Resolve effective feature flags for a hospital from platform `subscription_plans`."""
from __future__ import annotations

import uuid
from typing import Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plan_features import (
    ALL_FEATURE_KEYS,
    DEFAULT_FEATURES_BY_PLAN,
    normalize_plan_name,
)
from app.models.tenant import HospitalSubscription, SubscriptionPlanModel


async def _load_plan_row(
    db: AsyncSession, hospital_id: uuid.UUID
) -> Optional[SubscriptionPlanModel]:
    r = await db.execute(
        select(SubscriptionPlanModel)
        .join(HospitalSubscription, HospitalSubscription.plan_id == SubscriptionPlanModel.id)
        .where(HospitalSubscription.hospital_id == hospital_id)
    )
    return r.scalar_one_or_none()


def _merge_features(plan: Optional[SubscriptionPlanModel]) -> Dict[str, bool]:
    if not plan:
        return dict(DEFAULT_FEATURES_BY_PLAN["STANDARD"])
    pname = normalize_plan_name(plan.name)
    base = dict(DEFAULT_FEATURES_BY_PLAN.get(pname, DEFAULT_FEATURES_BY_PLAN["STANDARD"]))
    ov = plan.features if isinstance(plan.features, dict) else {}
    for k in ALL_FEATURE_KEYS:
        if k in ov:
            base[k] = bool(ov[k])
    return base


async def get_effective_feature_map(
    db: AsyncSession, hospital_id: uuid.UUID
) -> Dict[str, bool]:
    plan = await _load_plan_row(db, hospital_id)
    return _merge_features(plan)


async def get_plan_info_for_hospital(
    db: AsyncSession, hospital_id: uuid.UUID
) -> Tuple[Optional[str], Optional[str], Dict[str, bool]]:
    plan = await _load_plan_row(db, hospital_id)
    feats = _merge_features(plan)
    if not plan:
        return None, None, feats
    return plan.name, plan.display_name, feats


async def is_feature_enabled(
    db: AsyncSession, hospital_id: uuid.UUID, feature_key: str
) -> bool:
    plan = await _load_plan_row(db, hospital_id)
    m = _merge_features(plan)
    return bool(m.get(feature_key, False))
