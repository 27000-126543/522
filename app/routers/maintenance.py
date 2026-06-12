from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth import get_current_user, require_roles
from app.services.maintenance import MaintenancePlanService
from app.services.notification import NotificationService
from app.services.websocket_push import PushNotificationService
from app.models.models import (
    MaintenancePlan, MaintenanceTask, Turbine, User, UserRole, MaintenanceStatus
)
from app.schemas.schemas import (
    MaintenancePlanGenerate, MaintenanceTaskAssign,
    MaintenanceTaskUpdate, MaintenancePlanResponse, MaintenanceTaskResponse
)

router = APIRouter(prefix="/api/maintenance", tags=["预防维护计划"])


@router.post("/generate-weekly", response_model=List[MaintenancePlanResponse])
async def generate_weekly_plan(
    data: MaintenancePlanGenerate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    ))
):
    plans = MaintenancePlanService.generate_weekly_plan(
        db,
        wind_farm_id=data.wind_farm_id,
        week_start_date=data.week_start_date,
        generated_by=current_user.id
    )
    db.commit()
    return plans


@router.get("/plans", response_model=List[MaintenancePlanResponse])
async def list_maintenance_plans(
    wind_farm_id: Optional[int] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(MaintenancePlan)
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            query = query.filter(MaintenancePlan.wind_farm_id == current_user.wind_farm_id)
    if wind_farm_id:
        query = query.filter(MaintenancePlan.wind_farm_id == wind_farm_id)
    if status:
        query = query.filter(MaintenancePlan.status == status)
    if start_date:
        query = query.filter(MaintenancePlan.week_start_date >= start_date)
    if end_date:
        query = query.filter(MaintenancePlan.week_start_date <= end_date)

    plans = query.order_by(MaintenancePlan.week_start_date.desc()).offset(skip).limit(limit).all()
    return plans


@router.get("/plans/{plan_id}", response_model=MaintenancePlanResponse)
async def get_maintenance_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    plan = db.query(MaintenancePlan).filter(MaintenancePlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="维护计划不存在")
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id and plan.wind_farm_id and plan.wind_farm_id != current_user.wind_farm_id:
            raise HTTPException(status_code=403, detail="无权限访问该计划")
    return plan


@router.get("/tasks", response_model=List[MaintenanceTaskResponse])
async def list_maintenance_tasks(
    wind_farm_id: Optional[int] = None,
    turbine_id: Optional[int] = None,
    status: Optional[MaintenanceStatus] = None,
    assignee_id: Optional[int] = None,
    mine_only: bool = False,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(MaintenanceTask)
    if mine_only:
        query = query.filter(MaintenanceTask.assignee_id == current_user.id)
    elif assignee_id:
        query = query.filter(MaintenanceTask.assignee_id == assignee_id)

    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            turbine_ids = [t.id for t in db.query(Turbine.id).filter(
                Turbine.wind_farm_id == current_user.wind_farm_id
            ).all()]
            query = query.filter(MaintenanceTask.turbine_id.in_(turbine_ids))

    if wind_farm_id:
        turbine_ids = [t.id for t in db.query(Turbine.id).filter(
            Turbine.wind_farm_id == wind_farm_id
        ).all()]
        query = query.filter(MaintenanceTask.turbine_id.in_(turbine_ids))
    if turbine_id:
        query = query.filter(MaintenanceTask.turbine_id == turbine_id)
    if status:
        query = query.filter(MaintenanceTask.status == status)
    if start_date:
        query = query.filter(MaintenanceTask.scheduled_date >= start_date)
    if end_date:
        query = query.filter(MaintenanceTask.scheduled_date <= end_date)

    return query.order_by(MaintenanceTask.scheduled_date.asc()).offset(skip).limit(limit).all()


@router.post("/tasks/{task_id}/assign", response_model=MaintenanceTaskResponse)
async def assign_maintenance_task(
    task_id: int,
    assign_data: MaintenanceTaskAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    ))
):
    task = MaintenancePlanService.assign_task(
        db, task_id, assign_data.assignee_id
    )
    if not task:
        raise HTTPException(status_code=404, detail="维护任务不存在")

    turbine = db.query(Turbine).filter(Turbine.id == task.turbine_id).first()
    if turbine:
        assignee = db.query(User).filter(User.id == assign_data.assignee_id).first()
        if assignee:
            await PushNotificationService.push_maintenance(
                [assign_data.assignee_id], task, turbine, "assigned"
            )

    db.commit()
    return task


@router.put("/tasks/{task_id}/status", response_model=MaintenanceTaskResponse)
async def update_maintenance_task_status(
    task_id: int,
    update_data: MaintenanceTaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = db.query(MaintenanceTask).filter(MaintenanceTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="维护任务不存在")

    can_update = (
        current_user.role in [UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR]
        or task.assignee_id == current_user.id
    )
    if not can_update:
        raise HTTPException(status_code=403, detail="无权限修改任务状态")

    task = MaintenancePlanService.update_task_status(
        db, task_id, update_data.status,
        result_notes=update_data.result_notes,
        spare_parts_used=update_data.spare_parts_used,
        operator_id=current_user.id
    )

    turbine = db.query(Turbine).filter(Turbine.id == task.turbine_id).first()
    if turbine and update_data.status == MaintenanceStatus.COMPLETED:
        supervisors = NotificationService._get_supervisors_and_dispatchers(
            db, turbine.wind_farm_id
        )
        recipients = list(set(supervisors + ([task.assignee_id] if task.assignee_id else [])))
        await PushNotificationService.push_maintenance(
            recipients, task, turbine, "completed"
        )

    db.commit()
    return task
