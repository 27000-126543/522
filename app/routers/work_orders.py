from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth import get_current_user, require_roles
from app.services.work_order import WorkOrderAssignmentService
from app.services.persistent_fault import PersistentFaultService
from app.services.spare_part import SparePartService
from app.services.notification import NotificationService
from app.services.websocket_push import PushNotificationService
from app.models.models import (
    User, UserRole, Turbine, WorkOrder, OrderStatus,
    ProcessingRecord, FaultHistory, WindFarm
)
from app.schemas.schemas import (
    WorkOrderCreate, WorkOrderAssign, WorkOrderUpdateStatus,
    ProcessingRecordCreate, WorkOrderResponse, WorkOrderDetailResponse,
    ProcessingRecordResponse, ProcessingRecordWithDetailResponse,
    FaultHistoryResponse
)

router = APIRouter(prefix="/api/work-orders", tags=["工单管理"])


@router.post("", response_model=WorkOrderResponse)
async def create_work_order(
    order_data: WorkOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    ))
):
    turbine = db.query(Turbine).filter(Turbine.id == order_data.turbine_id).first()
    if not turbine:
        raise HTTPException(status_code=404, detail="风机不存在")

    order = WorkOrder(
        order_code=WorkOrderAssignmentService.generate_order_code(db),
        turbine_id=order_data.turbine_id,
        warning_id=order_data.warning_id,
        fault_type=order_data.fault_type,
        urgency_level=order_data.urgency_level,
        description=order_data.description,
        status=OrderStatus.PENDING,
        escalation_level=0
    )
    db.add(order)
    db.flush()

    wind_farm = turbine.wind_farm
    NotificationService.notify_work_order(db, order, turbine, wind_farm, "created")

    recipients = NotificationService._get_recipients_for_work_order(
        db, wind_farm.id, order.urgency_level
    )
    await PushNotificationService.push_work_order(recipients, order, turbine, "created")

    db.commit()
    db.refresh(order)
    return order


@router.get("", response_model=List[WorkOrderResponse])
async def list_work_orders(
    wind_farm_id: Optional[int] = None,
    turbine_id: Optional[int] = None,
    status: Optional[OrderStatus] = None,
    urgency: Optional[str] = None,
    fault_type: Optional[str] = None,
    assignee_id: Optional[int] = None,
    mine_only: bool = False,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(WorkOrder)

    if mine_only:
        query = query.filter(WorkOrder.assignee_id == current_user.id)
    elif assignee_id:
        query = query.filter(WorkOrder.assignee_id == assignee_id)

    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            tids = [t.id for t in db.query(Turbine.id).filter(
                Turbine.wind_farm_id == current_user.wind_farm_id
            ).all()]
            query = query.filter(WorkOrder.turbine_id.in_(tids))

    if wind_farm_id:
        tids = [t.id for t in db.query(Turbine.id).filter(
            Turbine.wind_farm_id == wind_farm_id
        ).all()]
        query = query.filter(WorkOrder.turbine_id.in_(tids))
    if turbine_id:
        query = query.filter(WorkOrder.turbine_id == turbine_id)
    if status:
        query = query.filter(WorkOrder.status == status)
    if urgency:
        query = query.filter(WorkOrder.urgency_level == urgency)
    if fault_type:
        query = query.filter(WorkOrder.fault_type == fault_type)
    if start_time:
        query = query.filter(WorkOrder.created_at >= start_time)
    if end_time:
        query = query.filter(WorkOrder.created_at <= end_time)

    return query.order_by(WorkOrder.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{order_id}", response_model=WorkOrderDetailResponse)
async def get_work_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            turbine = db.query(Turbine).filter(Turbine.id == order.turbine_id).first()
            if turbine and turbine.wind_farm_id != current_user.wind_farm_id:
                if order.assignee_id != current_user.id:
                    raise HTTPException(status_code=403, detail="无权限访问该工单")

    aggregated = {}
    for record in order.processing_records:
        if record.spare_parts and isinstance(record.spare_parts, list):
            for sp in record.spare_parts:
                if isinstance(sp, dict):
                    pid = sp.get("part_id") or sp.get("part_code")
                    if not pid:
                        continue
                    if pid not in aggregated:
                        aggregated[pid] = {
                            "part_id": sp.get("part_id"),
                            "part_code": sp.get("part_code"),
                            "part_name": sp.get("part_name", str(pid)),
                            "quantity": 0,
                            "unit_price": float(sp.get("unit_price", 0)),
                            "subtotal": 0.0
                        }
                    qty = int(sp.get("quantity", 0))
                    aggregated[pid]["quantity"] += qty
                    aggregated[pid]["subtotal"] += float(sp.get("subtotal", 0))

    order.spare_parts_detail = list(aggregated.values())
    return order


@router.post("/{order_id}/assign", response_model=WorkOrderResponse)
async def assign_work_order(
    order_id: int,
    assign_data: WorkOrderAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    ))
):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")

    assignee = db.query(User).filter(User.id == assign_data.assignee_id).first()
    if not assignee:
        raise HTTPException(status_code=404, detail="运维人员不存在")
    if assignee.role not in [UserRole.OPERATOR, UserRole.SUPERVISOR]:
        raise HTTPException(status_code=400, detail="该用户不是运维人员")

    order.assignee_id = assign_data.assignee_id
    order.status = OrderStatus.ASSIGNED
    order.assigned_at = datetime.now()
    order.assign_reason = assign_data.assign_reason or "手动分配"

    turbine = db.query(Turbine).filter(Turbine.id == order.turbine_id).first()
    wind_farm = turbine.wind_farm if turbine else None
    NotificationService.notify_work_order(db, order, turbine, wind_farm, "assigned")

    await PushNotificationService.push_work_order(
        [assignee.id], order, turbine, "assigned"
    )

    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/auto-assign", response_model=WorkOrderResponse)
async def auto_assign_work_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    ))
):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")

    turbine = db.query(Turbine).filter(Turbine.id == order.turbine_id).first()
    if not turbine:
        raise HTTPException(status_code=404, detail="风机不存在")

    assignee = WorkOrderAssignmentService.find_best_assignee(
        db, turbine, order.fault_type, order.urgency_level
    )
    if not assignee:
        raise HTTPException(status_code=400, detail="未找到合适的运维人员")

    order.assignee_id = assignee.id
    order.status = OrderStatus.ASSIGNED
    order.assigned_at = datetime.now()
    order.assign_reason = "智能匹配分配"

    wind_farm = turbine.wind_farm
    NotificationService.notify_work_order(db, order, turbine, wind_farm, "assigned")

    await PushNotificationService.push_work_order(
        [assignee.id], order, turbine, "assigned"
    )

    db.commit()
    db.refresh(order)
    return order


@router.put("/{order_id}/status", response_model=WorkOrderResponse)
async def update_order_status(
    order_id: int,
    status_data: WorkOrderUpdateStatus,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")

    can_change = (
        current_user.role in [UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR]
        or order.assignee_id == current_user.id
    )
    if not can_change:
        raise HTTPException(status_code=403, detail="无权限修改工单状态")

    new_status = status_data.status
    now = datetime.now()

    if new_status == OrderStatus.ACCEPTED and order.status == OrderStatus.ASSIGNED:
        order.accepted_at = now
    elif new_status == OrderStatus.IN_PROGRESS and order.status in [OrderStatus.ACCEPTED, OrderStatus.ASSIGNED]:
        if not order.accepted_at:
            order.accepted_at = now
        order.started_at = now
    elif new_status == OrderStatus.COMPLETED and order.status in [OrderStatus.IN_PROGRESS, OrderStatus.ACCEPTED]:
        if not order.started_at:
            order.started_at = order.accepted_at or now
        order.completed_at = now

        turbine = db.query(Turbine).filter(Turbine.id == order.turbine_id).first()
        if turbine:
            PersistentFaultService.record_fault_history(
                db, turbine, order, resolve_time=now
            )

        order.status = new_status
        turbine = db.query(Turbine).filter(Turbine.id == order.turbine_id).first()
        wind_farm = turbine.wind_farm if turbine else None
        NotificationService.notify_work_order(db, order, turbine, wind_farm, "completed")
        recipients = NotificationService._get_recipients_for_work_order(
            db, wind_farm.id if wind_farm else 0, order.urgency_level
        )
        await PushNotificationService.push_work_order(
            recipients, order, turbine, "completed"
        )
        db.commit()
        db.refresh(order)
        return order

    order.status = new_status
    db.commit()
    db.refresh(order)
    return order


@router.post("/processing-records", response_model=ProcessingRecordWithDetailResponse)
async def add_processing_record(
    record_data: ProcessingRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    order = db.query(WorkOrder).filter(
        WorkOrder.id == record_data.work_order_id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")

    can_add = (
        current_user.role in [UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR]
        or order.assignee_id == current_user.id
    )
    if not can_add:
        raise HTTPException(status_code=403, detail="无权限添加工单记录")

    turbine = db.query(Turbine).filter(Turbine.id == order.turbine_id).first()
    wind_farm_id = turbine.wind_farm_id if turbine and turbine.wind_farm_id else None

    parts_detail = None
    total_cost = 0.0
    enriched_spare_parts = record_data.spare_parts

    if record_data.spare_parts and wind_farm_id:
        ok, validated, info = SparePartService._validate_parts_availability(
            db, wind_farm_id, record_data.spare_parts
        )
        if not ok:
            detail = "; ".join([f"{f['item']}: {f['reason']}" for f in info["failed"]])
            raise HTTPException(status_code=400, detail=f"备件可用库存不足: {detail}")

        enriched_spare_parts = []
        for v in validated:
            enriched_spare_parts.append({
                "part_id": v["part_id"],
                "part_code": v["part_code"],
                "part_name": v["part_name"],
                "quantity": v["quantity"],
                "unit_price": v["unit_price"],
                "subtotal": v["subtotal"]
            })

    try:
        record = ProcessingRecord(
            work_order_id=record_data.work_order_id,
            operator_id=current_user.id,
            action=record_data.action,
            description=record_data.description,
            diagnosis=record_data.diagnosis,
            solution=record_data.solution,
            spare_parts=enriched_spare_parts,
            photos=record_data.photos,
            before_status=record_data.before_status,
            after_status=record_data.after_status
        )
        db.add(record)
        db.flush()

        if record_data.spare_parts and wind_farm_id:
            result = SparePartService.consume_parts_in_transaction(
                db, wind_farm_id, record_data.spare_parts
            )
            if not result["success"]:
                raise ValueError(result.get("error", "备件扣减失败"))
            order.total_cost += result["total_cost"]
            parts_detail = result["success_items"]
            total_cost = result["total_cost"]

        if record_data.action == "完成":
            order.completed_at = datetime.now()
            order.status = OrderStatus.COMPLETED
            if turbine:
                PersistentFaultService.record_fault_history(
                    db, turbine, order
                )
                wind_farm = turbine.wind_farm
                NotificationService.notify_work_order(db, order, turbine, wind_farm, "completed")
                recipients = NotificationService._get_recipients_for_work_order(
                    db, wind_farm.id if wind_farm else 0, order.urgency_level
                )
                await PushNotificationService.push_work_order(
                    recipients, order, turbine, "completed"
                )

        db.commit()
        db.refresh(record)

        response = ProcessingRecordWithDetailResponse(
            id=record.id,
            work_order_id=record.work_order_id,
            operator_id=record.operator_id,
            timestamp=record.timestamp,
            action=record.action,
            description=record.description,
            diagnosis=record.diagnosis,
            solution=record.solution,
            spare_parts=record.spare_parts,
            photos=record.photos,
            before_status=record.before_status,
            after_status=record.after_status,
            spare_parts_detail=parts_detail,
            total_cost=total_cost
        )
        return response

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"保存处理记录失败: {str(e)}")


@router.get("/{order_id}/records", response_model=List[ProcessingRecordResponse])
async def get_order_records(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")
    return db.query(ProcessingRecord).filter(
        ProcessingRecord.work_order_id == order_id
    ).order_by(ProcessingRecord.timestamp.asc()).all()


@router.get("/turbine/{turbine_id}/fault-history", response_model=List[FaultHistoryResponse])
async def get_turbine_fault_history(
    turbine_id: int,
    days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    turbine = db.query(Turbine).filter(Turbine.id == turbine_id).first()
    if not turbine:
        raise HTTPException(status_code=404, detail="风机不存在")
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id and current_user.wind_farm_id != turbine.wind_farm_id:
            raise HTTPException(status_code=403, detail="无权限访问")

    cutoff = datetime.now() - timedelta(days=days)
    return db.query(FaultHistory).filter(
        FaultHistory.turbine_id == turbine_id,
        FaultHistory.occurrence_time >= cutoff
    ).order_by(FaultHistory.occurrence_time.desc()).all()
