from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.models import (
    WorkOrder, OrderStatus, UserRole, SparePartStock,
    ReplenishmentRequest, ReplenishmentStatus
)
from app.config import settings
from app.services.spare_part import SparePartService
from app.services.maintenance import MaintenancePlanService
from app.services.report import ReportService
from app.services.notification import NotificationService
from app.services.work_order import WorkOrderAssignmentService
import logging

logger = logging.getLogger(__name__)


def check_order_timeouts():
    db: Session = SessionLocal()
    try:
        now = datetime.now()

        pending_orders = db.query(WorkOrder).filter(
            WorkOrder.status == OrderStatus.PENDING,
            WorkOrder.created_at <= now - timedelta(
                minutes=settings.ORDER_AUTO_ASSIGN_TIMEOUT_MINUTES
            )
        ).all()

        for order in pending_orders:
            turbine = order.turbine
            if turbine:
                assignee = WorkOrderAssignmentService.find_best_assignee(
                    db, turbine, order.fault_type, order.urgency_level
                )
                if assignee:
                    order.assignee_id = assignee.id
                    order.status = OrderStatus.ASSIGNED
                    order.assigned_at = now
                    order.assign_reason = "超时未分配，系统自动分配"

                    NotificationService.notify_work_order(
                        db, order, turbine, turbine.wind_farm, "assigned"
                    )

        assigned_orders = db.query(WorkOrder).filter(
            WorkOrder.status.in_([OrderStatus.ASSIGNED, OrderStatus.ACCEPTED, OrderStatus.IN_PROGRESS]),
        ).all()

        for order in assigned_orders:
            if order.status == OrderStatus.ASSIGNED:
                ref_time = order.assigned_at or order.created_at
                timeout_type = "未接单超时"
            else:
                ref_time = order.started_at or order.accepted_at or order.assigned_at or order.created_at
                timeout_type = "处理中超时"

            if ref_time and (now - ref_time) >= timedelta(
                minutes=settings.ORDER_ESCALATE_TIMEOUT_MINUTES
            ):
                current_level = order.escalation_level or 0
                if current_level < 3:
                    order.escalation_level = current_level + 1
                    order.status = OrderStatus.ESCALATED
                    order.escalated_at = now
                    order.escalation_reason = (
                        f"{timeout_type}（升级等级{current_level + 1}）"
                    )

                    turbine = order.turbine
                    if turbine:
                        NotificationService.notify_work_order(
                            db, order, turbine, turbine.wind_farm, "escalated"
                        )

        db.commit()
        logger.info(f"[定时任务] 工单超时检查完成 - 自动分配{len(pending_orders)}单, 升级处理")
    except Exception as e:
        logger.error(f"[定时任务] 工单超时检查失败: {e}")
        db.rollback()
    finally:
        db.close()


def check_stock_levels():
    db: Session = SessionLocal()
    try:
        low_stocks = SparePartService.check_all_stocks(db)
        delayed = SparePartService.check_delayed_deliveries(db)
        logger.info(f"[定时任务] 库存检查完成 - {len(low_stocks)}项库存告警, {len(delayed)}项采购延期")
    except Exception as e:
        logger.error(f"[定时任务] 库存检查失败: {e}")
    finally:
        db.close()


def generate_weekly_maintenance():
    db: Session = SessionLocal()
    try:
        plans = MaintenancePlanService.generate_weekly_plan(db)
        logger.info(f"[定时任务] 每周维保计划生成完成 - {len(plans)}个计划")
    except Exception as e:
        logger.error(f"[定时任务] 维保计划生成失败: {e}")
    finally:
        db.close()


def generate_daily_reports():
    db: Session = SessionLocal()
    try:
        yesterday = datetime.now() - timedelta(days=1)
        reports = ReportService.generate_daily_report(db, report_date=yesterday)
        logger.info(f"[定时任务] 日报表生成完成 - {len(reports)}份报表")
    except Exception as e:
        logger.error(f"[定时任务] 日报表生成失败: {e}")
    finally:
        db.close()


def init_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    scheduler.add_job(
        check_order_timeouts,
        IntervalTrigger(minutes=5),
        id="check_order_timeouts",
        replace_existing=True,
        name="工单超时检查"
    )

    scheduler.add_job(
        check_stock_levels,
        IntervalTrigger(hours=6),
        id="check_stock_levels",
        replace_existing=True,
        name="库存安全线检查"
    )

    scheduler.add_job(
        generate_weekly_maintenance,
        CronTrigger(day_of_week='mon', hour=1, minute=0),
        id="generate_weekly_maintenance",
        replace_existing=True,
        name="每周维保计划生成（每周一1:00）"
    )

    scheduler.add_job(
        generate_daily_reports,
        CronTrigger(hour=2, minute=0),
        id="generate_daily_reports",
        replace_existing=True,
        name="每日运维报表生成（每日2:00）"
    )

    return scheduler


def run_bootstrap_tasks():
    db: Session = SessionLocal()
    try:
        logger.info("[启动任务] 执行启动时数据检查...")
        check_order_timeouts()
        check_stock_levels()
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        existing = db.query(WorkOrder).filter(
            WorkOrder.created_at >= today
        ).first()
        if not existing:
            check_stock_levels()
        logger.info("[启动任务] 完成")
    except Exception as e:
        logger.error(f"[启动任务] 失败: {e}")
    finally:
        db.close()
