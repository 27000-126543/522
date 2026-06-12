from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth import get_current_user, require_roles
from app.services.health_scoring import HealthScoreService
from app.services.work_order import WorkOrderAssignmentService
from app.services.notification import NotificationService
from app.services.websocket_push import PushNotificationService, ws_manager
from app.models.models import (
    User, UserRole, Turbine, SensorData, HealthRecord,
    Warning, WarningLevel, WorkOrder, WindFarm
)
from app.schemas.schemas import (
    SensorDataCreate, SensorDataResponse, HealthScoreResult,
    HealthRecordResponse, WarningResponse, WarningAcknowledge
)
import uuid

router = APIRouter(prefix="/api/sensors", tags=["传感器数据与健康评分"])


@router.post("/data", response_model=HealthScoreResult)
async def upload_sensor_data(
    sensor_data: SensorDataCreate,
    db: Session = Depends(get_db)
):
    turbine = db.query(Turbine).filter(
        Turbine.turbine_code == sensor_data.turbine_code
    ).first()
    if not turbine:
        raise HTTPException(status_code=404, detail=f"风机 {sensor_data.turbine_code} 不存在")

    data_dict = sensor_data.model_dump()
    data_dict.pop("turbine_code", None)
    sensor_record = SensorData(turbine_id=turbine.id, **data_dict)
    db.add(sensor_record)
    db.flush()

    result = HealthScoreService.calculate_health_score(sensor_record, turbine.id)

    health_record = HealthRecord(
        turbine_id=turbine.id,
        sensor_data_id=sensor_record.id,
        health_score=result.overall_score,
        vibration_score=result.vibration_score,
        temperature_score=result.temperature_score,
        power_score=result.power_score,
        noise_score=result.noise_score,
        other_score=result.other_score,
        warning_level=result.warning_level
    )
    db.add(health_record)

    turbine.health_score = result.overall_score
    turbine.health_status = result.warning_level

    if result.warning_level:
        fault_type = WorkOrderAssignmentService.determine_fault_type(
            result.abnormal_params, result
        )
        urgency = WorkOrderAssignmentService.determine_urgency(
            result.warning_level, fault_type
        )

        existing_warning = db.query(Warning).filter(
            Warning.turbine_id == turbine.id,
            Warning.fault_type == fault_type,
            Warning.timestamp >= datetime.now() - timedelta(hours=1)
        ).first()

        if not existing_warning:
            warning = Warning(
                turbine_id=turbine.id,
                warning_code=f"WN{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4]}",
                warning_level=result.warning_level,
                fault_type=fault_type,
                urgency_level=urgency,
                health_record_id=health_record.id,
                description=f"健康评分 {result.overall_score}，{result.warning_level.value} 级预警。"
                            f"异常参数: {', '.join(result.abnormal_params.keys()) if result.abnormal_params else '综合指标异常'}",
                abnormal_values=result.abnormal_params
            )
            db.add(warning)
            db.flush()

            if result.warning_level in [WarningLevel.ORANGE, WarningLevel.RED]:
                order = WorkOrder(
                    order_code=WorkOrderAssignmentService.generate_order_code(db),
                    turbine_id=turbine.id,
                    warning_id=warning.id,
                    fault_type=fault_type,
                    urgency_level=urgency,
                    description=f"自动生成工单: {warning.description}",
                    status="pending",
                    escalation_level=0
                )
                db.add(order)
                db.flush()

                warning.work_order_id = order.id

                assignee = WorkOrderAssignmentService.find_best_assignee(
                    db, turbine, fault_type, urgency
                )
                if assignee:
                    order.assignee_id = assignee.id
                    order.status = "assigned"
                    order.assigned_at = datetime.now()
                    order.assign_reason = f"智能匹配 (技能:{', '.join(assignee.skills or [])}, 距离最优, 工作量均衡)"

                wind_farm = turbine.wind_farm
                NotificationService.notify_warning(db, warning, turbine, wind_farm)
                NotificationService.notify_work_order(
                    db, order, turbine, wind_farm, "created"
                )

                warning_recipients = NotificationService._get_recipients_for_warning(
                    db, wind_farm.id, urgency
                )
                await PushNotificationService.push_warning(warning_recipients, warning, turbine)

                order_recipients = NotificationService._get_recipients_for_work_order(
                    db, wind_farm.id, urgency
                )
                if assignee:
                    await PushNotificationService.push_work_order(
                        [assignee.id], order, turbine, "assigned"
                    )
                await PushNotificationService.push_work_order(
                    order_recipients, order, turbine, "created"
                )

    db.commit()
    return result


@router.get("/data/{turbine_id}", response_model=List[SensorDataResponse])
async def get_sensor_history(
    turbine_id: int,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    turbine = db.query(Turbine).filter(Turbine.id == turbine_id).first()
    if not turbine:
        raise HTTPException(status_code=404, detail="风机不存在")
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id and current_user.wind_farm_id != turbine.wind_farm_id:
            raise HTTPException(status_code=403, detail="无权限访问该风机数据")

    query = db.query(SensorData).filter(SensorData.turbine_id == turbine_id)
    if start_time:
        query = query.filter(SensorData.timestamp >= start_time)
    if end_time:
        query = query.filter(SensorData.timestamp <= end_time)
    return query.order_by(SensorData.timestamp.desc()).limit(limit).all()


@router.get("/health/{turbine_id}/records", response_model=List[HealthRecordResponse])
async def get_health_records(
    turbine_id: int,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    turbine = db.query(Turbine).filter(Turbine.id == turbine_id).first()
    if not turbine:
        raise HTTPException(status_code=404, detail="风机不存在")
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id and current_user.wind_farm_id != turbine.wind_farm_id:
            raise HTTPException(status_code=403, detail="无权限访问该风机数据")

    query = db.query(HealthRecord).filter(HealthRecord.turbine_id == turbine_id)
    if start_time:
        query = query.filter(HealthRecord.timestamp >= start_time)
    if end_time:
        query = query.filter(HealthRecord.timestamp <= end_time)
    return query.order_by(HealthRecord.timestamp.desc()).limit(limit).all()


@router.get("/warnings", response_model=List[WarningResponse])
async def list_warnings(
    wind_farm_id: Optional[int] = None,
    level: Optional[WarningLevel] = None,
    acknowledged: Optional[bool] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(Warning)
    if wind_farm_id:
        turbine_ids = [t.id for t in db.query(Turbine.id).filter(Turbine.wind_farm_id == wind_farm_id).all()]
        query = query.filter(Warning.turbine_id.in_(turbine_ids))
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            turbine_ids = [t.id for t in db.query(Turbine.id).filter(
                Turbine.wind_farm_id == current_user.wind_farm_id
            ).all()]
            query = query.filter(Warning.turbine_id.in_(turbine_ids))
    if level:
        query = query.filter(Warning.warning_level == level)
    if acknowledged is not None:
        query = query.filter(Warning.is_acknowledged == acknowledged)
    if start_time:
        query = query.filter(Warning.timestamp >= start_time)
    if end_time:
        query = query.filter(Warning.timestamp <= end_time)
    return query.order_by(Warning.timestamp.desc()).offset(skip).limit(limit).all()


@router.post("/warnings/acknowledge", response_model=WarningResponse)
async def acknowledge_warning(
    data: WarningAcknowledge,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    warning = db.query(Warning).filter(Warning.id == data.warning_id).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警不存在")
    warning.is_acknowledged = True
    warning.acknowledged_by = current_user.id
    warning.acknowledged_at = datetime.now()
    db.commit()
    db.refresh(warning)
    return warning
