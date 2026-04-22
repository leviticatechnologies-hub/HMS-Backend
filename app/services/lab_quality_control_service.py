"""
Service layer for Quality Control workflows UI.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabQcMaterial, LabQcRule, LabQcRun
from app.schemas.lab_quality_control import (
    QcMaterialRow,
    QcRuleRow,
    QcRunRow,
    QcStatCards,
    QcWorkflowActionResponse,
    QualityControlDashboardResponse,
    QualityControlMeta,
    RecordQcRunRequest,
    RecordQcRunResponse,
)


class LabQualityControlService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    def _demo_qc_runs(self) -> list[QcRunRow]:
        return [
            QcRunRow(
                qc_id="QC-2024-001",
                test="CBC",
                qc_material="Hematology Control",
                lot_number="LOT-123",
                date="2024-01-15",
                operator="Lab Tech Ravi",
                status="PASSED",
                observed_value=12.5,
            ),
            QcRunRow(
                qc_id="QC-2024-002",
                test="Glucose",
                qc_material="Chemistry Control Level 1",
                lot_number="LOT-456",
                date="2024-01-15",
                operator="Lab Tech Priya",
                status="WARNING",
                observed_value=105.0,
            ),
            QcRunRow(
                qc_id="QC-2024-003",
                test="Creatinine",
                qc_material="Chemistry Control Level 2",
                lot_number="LOT-789",
                date="2024-01-14",
                operator="Lab Tech Sanjay",
                status="FAILED",
                observed_value=2.5,
            ),
            QcRunRow(
                qc_id="QC-2024-004",
                test="Thyroid",
                qc_material="Hormone Control",
                lot_number="LOT-901",
                date="2024-01-14",
                operator="Lab Tech Neha",
                status="PASSED",
                observed_value=3.2,
            ),
        ]

    def _demo_materials(self) -> list[QcMaterialRow]:
        return [
            QcMaterialRow("Hematology Control", "Hematology", "Sysmex", "LOT-123", "2024-06-30", "2-8°C", 25),
            QcMaterialRow("Chemistry Control Level 1", "Chemistry", "Bio-Rad", "LOT-456", "2024-05-15", "2-8°C", 30),
            QcMaterialRow("Chemistry Control Level 2", "Chemistry", "Bio-Rad", "LOT-789", "2024-05-15", "2-8°C", 28),
        ]

    def _demo_rules(self) -> list[QcRuleRow]:
        return [
            QcRuleRow("1-3s Rule", "One point beyond 3 SD from mean", "Westgard", "Reject run, investigate", "HIGH"),
            QcRuleRow("2-2s Rule", "Two consecutive points beyond 2 SD on same side", "Westgard", "Reject run, investigate", "HIGH"),
            QcRuleRow("R-4s Rule", "Range of 4 SD between two points", "Westgard", "Reject run", "HIGH"),
            QcRuleRow("4-1s Rule", "Four consecutive points beyond 1 SD on same side", "Westgard", "Warning, check trend", "MEDIUM"),
        ]

    async def dashboard(self, *, demo: bool = False) -> QualityControlDashboardResponse:
        if demo:
            runs = self._demo_qc_runs()
            mats = self._demo_materials()
            rules = self._demo_rules()
        else:
            runs, mats, rules = await self._db_rows()
        stats = QcStatCards(
            todays_qc_runs=0 if demo else 0,
            passed_runs=sum(1 for r in runs if r.status == "PASSED"),
            warning_runs=sum(1 for r in runs if r.status == "WARNING"),
            failed_runs=sum(1 for r in runs if r.status == "FAILED"),
        )
        return QualityControlDashboardResponse(
            meta=QualityControlMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=False,
                demo_data=demo,
            ),
            stats=stats,
            qc_runs=runs,
            materials_inventory=mats,
            rules=rules,
            workflow_actions=["LEVEY_JENNINGS_CHART", "QC_COMPLIANCE_REPORT", "QC_ALERTS"],
        )

    async def record_qc_run(self, payload: RecordQcRunRequest) -> RecordQcRunResponse:
        if payload.observed_value <= 0:
            status = "FAILED"
        elif payload.observed_value > 100:
            status = "WARNING"
        else:
            status = "PASSED"
        qc_id = f"QC-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        rec = LabQcRun(
            hospital_id=self.hospital_id,
            qc_id=qc_id,
            test=payload.test,
            qc_material=payload.qc_material,
            lot_number=payload.lot_number,
            run_date=payload.date,
            operator=payload.operator,
            status=status,
            observed_value=payload.observed_value,
        )
        self.db.add(rec)
        await self.db.commit()
        return RecordQcRunResponse(
            message="QC run recorded successfully.",
            qc_id=qc_id,
            status=status,  # type: ignore[arg-type]
        )

    async def workflow_action(self, action: str) -> QcWorkflowActionResponse:
        return QcWorkflowActionResponse(
            message=f"{action} initiated successfully.",
            action=action,
        )

    async def _db_rows(self):
        runs = (await self.db.execute(select(LabQcRun).where(LabQcRun.hospital_id == self.hospital_id))).scalars().all()
        mats = (await self.db.execute(select(LabQcMaterial).where(LabQcMaterial.hospital_id == self.hospital_id))).scalars().all()
        rules = (await self.db.execute(select(LabQcRule).where(LabQcRule.hospital_id == self.hospital_id))).scalars().all()
        return (
            [QcRunRow(qc_id=r.qc_id, test=r.test, qc_material=r.qc_material, lot_number=r.lot_number, date=r.run_date, operator=r.operator, status=r.status, observed_value=float(r.observed_value)) for r in runs],
            [QcMaterialRow(material_name=m.material_name, material_type=m.material_type, manufacturer=m.manufacturer, lot_number=m.lot_number, expiry_date=m.expiry_date, storage=m.storage, quantity=m.quantity) for m in mats],
            [QcRuleRow(rule_name=x.rule_name, description=x.description, rule_type=x.rule_type, action_required=x.action_required, priority=x.priority) for x in rules],
        )

