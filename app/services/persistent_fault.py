from typing import Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.models import (
    Turbine, WorkOrder, FaultHistory, FaultType, OrderStatus,
    FaultHistory, WindFarm
)
from app.config import settings
from app.services.notification import NotificationService


class PersistentFaultService:
    @staticmethod
    def check_persistent_fault(
        db: Session,
        turbine: Turbine,
        fault_type: FaultType,
        completed_work_order_id: Optional[int] = None
    ) -> dict:
        cutoff_date = datetime.now() - timedelta(days=settings.PERSISTENT_FAULT_DAYS)

        recent_faults = db.query(FaultHistory).filter(
            FaultHistory.turbine_id == turbine.id,
            FaultHistory.fault_type == fault_type,
            FaultHistory.occurrence_time >= cutoff_date
        ).all()

        fault_count = len(recent_faults)
        result = {
            "is_persistent": fault_count >= 3,
            "count": fault_count,
            "needs_deep_inspection": fault_count >= 3
        }

        if fault_count >= 3:
            turbine.is_persistent_risk = True
            turbine.persistent_risk_type = fault_type.value

            for fh in recent_faults:
                fh.is_persistent = True
                fh.deep_inspection_required = True

            NotificationService.notify_persistent_fault(
                db, turbine, fault_type, fault_count
            )

        db.flush()
        return result

    @staticmethod
    def record_fault_history(
        db: Session,
        turbine: Turbine,
        work_order: WorkOrder,
        resolve_time: Optional[datetime] = None
    ) -> FaultHistory:
        fault_history = FaultHistory(
            turbine_id=turbine.id,
            work_order_id=work_order.id,
            fault_type=work_order.fault_type,
            occurrence_time=work_order.created_at,
            resolve_time=resolve_time or datetime.now()
        )
        db.add(fault_history)
        db.flush()

        PersistentFaultService.check_persistent_fault(
            db, turbine, work_order.fault_type
        )

        return fault_history

    @staticmethod
    def complete_deep_inspection(
        db: Session,
        turbine_id: int,
        fault_history_id: int,
        operator_id: int,
        notes: str
    ) -> Optional[FaultHistory]:
        fh = db.query(FaultHistory).filter(
            FaultHistory.id == fault_history_id,
            FaultHistory.turbine_id == turbine_id
        ).first()
        if not fh:
            return None
        fh.deep_inspection_completed = True
        fh.notes = (fh.notes or "") + f"\n[深度检查 - {datetime.now()}] {notes}"

        all_done = db.query(FaultHistory).filter(
            FaultHistory.turbine_id == turbine_id,
            FaultHistory.is_persistent == True,
            FaultHistory.deep_inspection_required == True,
            FaultHistory.deep_inspection_completed == False
        ).count() == 0

        if all_done:
            turbine = db.query(Turbine).filter(Turbine.id == turbine_id).first()
            if turbine:
                turbine.is_persistent_risk = False
                turbine.persistent_risk_type = None

        db.flush()
        return fh
