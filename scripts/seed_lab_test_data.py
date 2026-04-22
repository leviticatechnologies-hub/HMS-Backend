"""
Legacy lab catalogue seed script — removed.

The full lab module (tests, orders, samples, reports) was replaced with a minimal
equipment + maintenance API. This script no longer applies.

Use the equipment endpoints under /api/v1/lab/equipment-qc/equipment instead.
"""

def main() -> None:
    raise SystemExit(
        "scripts/seed_lab_test_data.py is obsolete: lab catalogue models were removed. "
        "See app/api/v1/routers/lab/lab_equipment.py for the current lab surface."
    )


if __name__ == "__main__":
    main()
