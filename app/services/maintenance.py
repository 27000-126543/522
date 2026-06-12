from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.models import (
    MaintenancePlan, MaintenanceTask, Turbine, WindFarm,
    MaintenanceStatus, FaultHistory, WorkOrder, User, UserRole,
    WarningLevel
)
from app.services.work_order import WorkOrderAssignmentService
from app.services.notification import NotificationService
import random


class MaintenancePlanService:
    STANDARD_MAINTENANCE_TASKS = [
        {
            "task_type": "常规检查",
            "description": "对风机进行常规外观检查、紧固件检查、润滑系统检查",
            "estimated_hours": 2.0,
            "checklist": [
                "检查塔筒外观",
                "检查机舱密封",
                "检查螺栓紧固情况",
                "检查润滑系统油位",
                "检查偏航系统"
            ],
            "interval_weeks": 4
        },
        {
            "task_type": "油品检测",
            "description": "齿轮箱油、液压油采样检测",
            "estimated_hours": 1.5,
            "checklist": [
                "齿轮箱油采样",
                "液压油采样",
                "油品外观检查",
                "记录检测数据"
            ],
            "interval_weeks": 8
        },
        {
            "task_type": "叶片检查",
            "description": "叶片外观检查、防雷系统检测",
            "estimated_hours": 3.0,
            "checklist": [
                "叶片外观检查",
                "叶片前缘检查",
                "防雷系统电阻测试",
                "叶根螺栓检查"
            ],
            "interval_weeks": 12
        },
        {
            "task_type": "电气系统检查",
            "description": "电气柜、变频器、变压器检查",
            "estimated_hours": 2.5,
            "checklist": [
                "电气柜清洁检查",
                "变频器参数检查",
                "接线端子紧固",
                "绝缘电阻测试",
                "变压器油温检查"
            ],
            "interval_weeks": 8
        }
    ]

    @staticmethod
    def generate_plan_code(db: Session) -> str:
        prefix = "MP" + datetime.now().strftime("%Y%m%d")
        last_plan = db.query(MaintenancePlan).filter(
            MaintenancePlan.plan_code.like(f"{prefix}%")
        ).order_by(MaintenancePlan.id.desc()).first()
        if last_plan:
            try:
                seq = int(last_plan.plan_code[-3:]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1
        return f"{prefix}{seq:03d}"

    @staticmethod
    def _get_weather_forecast(wind_farm_id: int, week_start: datetime) -> Dict[str, Any]:
        conditions = ["晴", "多云", "小雨", "中雨", "大风"]
        forecast = {}
        for i in range(7):
            day = week_start + timedelta(days=i)
            condition = random.choice(conditions)
            forecast[day.strftime("%Y-%m-%d")] = {
                "condition": condition,
                "wind_speed_avg": round(random.uniform(3, 15), 1),
                "temperature_high": round(random.uniform(15, 32), 1),
                "temperature_low": round(random.uniform(5, 20), 1),
                "is_suitable": condition not in ["中雨", "大风"]
            }
        return forecast

    @staticmethod
    def _identify_high_priority_turbines(
        db: Session,
        turbines: List[Turbine]
    ) -> List[tuple]:
        priority_turbines = []
        thirty_days_ago = datetime.now() - timedelta(days=30)

        for t in turbines:
            priority = 0
            reasons = []

            if t.is_persistent_risk:
                priority += 50
                reasons.append(f"顽固隐患: {t.persistent_risk_type}")

            if t.health_score < 50:
                priority += 40
                reasons.append(f"健康评分低: {t.health_score}")
            elif t.health_score < 70:
                priority += 20
                reasons.append(f"健康评分: {t.health_score}")

            recent_faults = db.query(FaultHistory).filter(
                FaultHistory.turbine_id == t.id,
                FaultHistory.occurrence_time >= thirty_days_ago
            ).count()
            if recent_faults >= 3:
                priority += recent_faults * 10
                reasons.append(f"近30天{recent_faults}次故障")

            if t.last_maintenance_date:
                days_since = (datetime.now() - t.last_maintenance_date).days
                if days_since > 90:
                    priority += 30
                    reasons.append(f"距上次维保{days_since}天")

            if priority > 0:
                priority_turbines.append((t, priority, reasons))

        priority_turbines.sort(key=lambda x: x[1], reverse=True)
        return priority_turbines

    @staticmethod
    def generate_weekly_plan(
        db: Session,
        wind_farm_id: Optional[int] = None,
        week_start_date: Optional[datetime] = None,
        generated_by: Optional[int] = None
    ) -> List[MaintenancePlan]:
        if week_start_date is None:
            today = datetime.now()
            week_start_date = today - timedelta(days=today.weekday())
        week_start_date = week_start_date.replace(hour=0, minute=0, second=0, microsecond=0)

        plans = []
        farm_ids = [wind_farm_id] if wind_farm_id else [
            wf.id for wf in db.query(WindFarm).all()
        ]

        for wf_id in farm_ids:
            turbines = db.query(Turbine).filter(
                Turbine.wind_farm_id == wf_id
            ).all()

            if not turbines:
                continue

            plan = MaintenancePlan(
                plan_code=MaintenancePlanService.generate_plan_code(db),
                week_start_date=week_start_date,
                wind_farm_id=wf_id,
                generated_by=generated_by,
                is_auto_generated=True,
                weather_forecast=MaintenancePlanService._get_weather_forecast(
                    wf_id, week_start_date
                ),
                status="active"
            )
            db.add(plan)
            db.flush()

            high_priority = MaintenancePlanService._identify_high_priority_turbines(db, turbines)
            priority_turbine_ids = set(t[0].id for t in high_priority)

            tasks = []
            day_offset = 0

            for turbine, p_score, reasons in high_priority:
                task_date = week_start_date + timedelta(days=min(day_offset, 6))
                task = MaintenanceTask(
                    plan_id=plan.id,
                    turbine_id=turbine.id,
                    task_type="重点维护",
                    description="优先级维护任务: " + "; ".join(reasons),
                    scheduled_date=task_date,
                    estimated_hours=4.0,
                    status=MaintenanceStatus.PLANNED,
                    checklist=[
                        "全面检查故障相关部件",
                        "执行标准维护流程",
                        "确认隐患排除",
                        "记录所有检查结果"
                    ]
                )
                tasks.append(task)
                day_offset += 1

            standard_count = max(0, len(turbines) - len(high_priority))
            remaining_turbines = [t for t in turbines if t.id not in priority_turbine_ids]
            random.shuffle(remaining_turbines)

            for i, turbine in enumerate(remaining_turbines):
                task_template = MaintenancePlanService.STANDARD_MAINTENANCE_TASKS[
                    i % len(MaintenancePlanService.STANDARD_MAINTENANCE_TASKS)
                ]
                task_date = week_start_date + timedelta(days=min(day_offset, 6))

                task = MaintenanceTask(
                    plan_id=plan.id,
                    turbine_id=turbine.id,
                    task_type=task_template["task_type"],
                    description=task_template["description"],
                    scheduled_date=task_date,
                    estimated_hours=task_template["estimated_hours"],
                    status=MaintenanceStatus.PLANNED,
                    checklist=task_template["checklist"]
                )
                tasks.append(task)
                day_offset += 1

            db.add_all(tasks)
            db.flush()

            operators = db.query(User).filter(
                User.wind_farm_id == wf_id,
                User.role == UserRole.OPERATOR,
                User.is_active == True
            ).all()

            if operators:
                scheduled_tasks = sorted(tasks, key=lambda x: x.scheduled_date)
                for idx, task in enumerate(scheduled_tasks):
                    operator = operators[idx % len(operators)]
                    task.assignee_id = operator.id
                    task.assigned_at = datetime.now()
                    task.status = MaintenanceStatus.ASSIGNED
                    NotificationService.notify_maintenance(
                        db, task, turbines[0] if turbines else None, "assigned"
                    )

            plan.tasks = tasks
            plans.append(plan)

        db.flush()
        return plans

    @staticmethod
    def assign_task(
        db: Session,
        task_id: int,
        assignee_id: int
    ) -> Optional[MaintenanceTask]:
        task = db.query(MaintenanceTask).filter(
            MaintenanceTask.id == task_id
        ).first()
        if not task:
            return None
        task.assignee_id = assignee_id
        task.assigned_at = datetime.now()
        task.status = MaintenanceStatus.ASSIGNED
        db.flush()

        turbine = db.query(Turbine).filter(Turbine.id == task.turbine_id).first()
        if turbine:
            NotificationService.notify_maintenance(db, task, turbine, "assigned")

        return task

    @staticmethod
    def update_task_status(
        db: Session,
        task_id: int,
        status: MaintenanceStatus,
        result_notes: Optional[str] = None,
        spare_parts_used: Optional[List[Dict[str, Any]]] = None,
        operator_id: Optional[int] = None
    ) -> Optional[MaintenanceTask]:
        task = db.query(MaintenanceTask).filter(
            MaintenanceTask.id == task_id
        ).first()
        if not task:
            return None

        task.status = status
        if status == MaintenanceStatus.IN_PROGRESS:
            task.started_at = datetime.now()
        elif status == MaintenanceStatus.COMPLETED:
            task.completed_at = datetime.now()
            turbine = db.query(Turbine).filter(Turbine.id == task.turbine_id).first()
            if turbine:
                turbine.last_maintenance_date = datetime.now()
        if result_notes:
            task.result_notes = result_notes
        if spare_parts_used:
            task.spare_parts_used = spare_parts_used

        db.flush()

        turbine = db.query(Turbine).filter(Turbine.id == task.turbine_id).first()
        if turbine and status == MaintenanceStatus.COMPLETED:
            NotificationService.notify_maintenance(db, task, turbine, "completed")

        return task
