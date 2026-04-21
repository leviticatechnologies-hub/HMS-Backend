"""
Human-readable resource and action labels for Hospital Admin audit logs (UI + API).
"""
import re
from typing import Any, Dict, Optional

# Order: first matching rule wins (most specific patterns first).
_RESOURCE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/platform-settings/features"), "Subscription"),
    (re.compile(r"/reports/bed-occupancy"), "Report"),
    (re.compile(r"/reports/department-performance"), "Report"),
    (re.compile(r"/reports/revenue-summary"), "Report"),
    (re.compile(r"/dashboard/overview"), "Dashboard"),
    (re.compile(r"/dashboard/staff-stats"), "Dashboard"),
    (re.compile(r"/dashboard/appointment-stats"), "Dashboard"),
    (re.compile(r"/departments/assign-staff"), "Department"),
    (re.compile(r"/departments/unassign-staff"), "Department"),
    (re.compile(r"/departments/.+/staff"), "Department"),
    (re.compile(r"/departments"), "Department"),
    (re.compile(r"/staff/.+/reset-password"), "User"),
    (re.compile(r"/staff/doctors/"), "User"),
    (re.compile(r"/staff/nurses/"), "User"),
    (re.compile(r"/staff/receptionists/"), "User"),
    (re.compile(r"/staff/lab-techs/"), "User"),
    (re.compile(r"/staff/pharmacists/"), "User"),
    (re.compile(r"/staff"), "User"),
    (re.compile(r"/appointments"), "Appointment"),
    (re.compile(r"/patients"), "Patient"),
    (re.compile(r"/wards"), "Ward"),
    (re.compile(r"/beds"), "Bed"),
    (re.compile(r"/admissions"), "Admission"),
    (re.compile(r"/reports"), "Report"),
    (re.compile(r"/dashboard"), "Dashboard"),
    (re.compile(r"/audit-logs"), "Audit"),
]


def resource_label_from_path(path: str) -> str:
    """Map request path to a short resource name for the audit table (e.g. Department, Settings)."""
    if not path:
        return "System"
    p = path.split("?", 1)[0]
    for pattern, label in _RESOURCE_RULES:
        if pattern.search(p):
            return label
    return "System"


def action_display_from_code(action: str) -> str:
    """Map stored action code to UI title case (View, Create, Update, Delete, Login, …)."""
    a = (action or "").upper()
    return {
        "VIEW": "View",
        "CREATE": "Create",
        "UPDATE": "Update",
        "DELETE": "Delete",
        "LOGIN": "Login",
        "LOGOUT": "Logout",
        "EXPORT": "Export",
    }.get(a, action.title() if action else "Unknown")


def resource_from_row(new_values: Optional[Dict[str, Any]], description: str = "") -> str:
    """Resolve resource label for older rows missing `resource` in new_values."""
    if isinstance(new_values, dict):
        r = new_values.get("resource") or new_values.get("resource_label")
        if isinstance(r, str) and r.strip():
            return r.strip()
        p = new_values.get("path")
        if isinstance(p, str) and p:
            return resource_label_from_path(p)
    if description:
        # e.g. "GET /api/v1/hospital-admin/departments"
        m = re.search(r"\s(/api/v1/hospital-admin\S*)", description)
        if m:
            return resource_label_from_path(m.group(1))
    return "System"
