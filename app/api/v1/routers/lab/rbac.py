"""
Role lists for /api/v1/lab/* routes.

- All lab read (GET) endpoints allow lab staff plus **HOSPITAL_ADMIN** so admins can
  monitor the lab from the same hospital tenant.
- **RECEPTIONIST** is intentionally not included for lab APIs; front-desk
  `GET /receptionist/dashboard` and `GET /receptionist/profile` stay separate.

Use ``Depends(require_roles(LAB_GET_ROLES))`` on lab GET handlers.
"""
from __future__ import annotations

from typing import Final, List

# Order: operational lab roles first, then hospital oversight.
LAB_GET_ROLES: Final[List[str]] = [
    "LAB_TECH",
    "LAB_SUPERVISOR",
    "LAB_ADMIN",
    "PATHOLOGIST",
    "HOSPITAL_ADMIN",
]
