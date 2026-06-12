from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.models import Notification, User, UserRole, WindFarm
from datetime import datetime


class NotificationService:
    @staticmethod
    def create_notification(
        db: Session,
        user_id: int,
        notification_type: str,
        title: str,
        content: str,
        related_type: Optional[str] = None,
        related_id: Optional[int] = None,
        channel: str = "system"
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            content=content,
            related_type=related_type,
            related_id=related_id,
            channel=channel
        )
        db.add(notification)
        db.flush()
        return notification

    @staticmethod
    def notify_warning(db: Session, warning, turbine, wind_farm):
        title = f"【预警】风机 {turbine.turbine_code} {warning.warning_level.value.upper()}级预警"
        content = f"故障类型: {warning.fault_type.value}, 紧急程度: {warning.urgency_level.value}\n{warning.description or ''}"

        recipients = NotificationService._get_recipients_for_warning(
            db, wind_farm.id, warning.urgency_level
        )

        for user_id in recipients:
            NotificationService.create_notification(
                db, user_id, "warning", title, content,
                related_type="warning", related_id=warning.id
            )

    @staticmethod
    def notify_work_order(db: Session, work_order, turbine, wind_farm, event_type: str):
        event_titles = {
            "created": f"【工单】风机 {turbine.turbine_code} 新工单创建",
            "assigned": f"【工单】您有新工单待处理 - 风机 {turbine.turbine_code}",
            "escalated": f"【升级】风机 {turbine.turbine_code} 工单已升级",
            "completed": f"【完成】风机 {turbine.turbine_code} 工单已完成"
        }
        title = event_titles.get(event_type, f"【工单】风机 {turbine.turbine_code} 状态变更")
        content = (
            f"工单编号: {work_order.order_code}\n"
            f"故障类型: {work_order.fault_type.value}\n"
            f"紧急程度: {work_order.urgency_level.value}\n"
            f"当前状态: {work_order.status.value}\n"
            f"{work_order.description or ''}"
        )

        if event_type == "assigned" and work_order.assignee_id:
            recipients = [work_order.assignee_id]
        elif event_type == "escalated":
            recipients = NotificationService._get_recipients_for_escalation(
                db, wind_farm.id, work_order.escalation_level
            )
        else:
            recipients = NotificationService._get_recipients_for_work_order(
                db, wind_farm.id, work_order.urgency_level
            )

        for user_id in recipients:
            NotificationService.create_notification(
                db, user_id, "work_order", title, content,
                related_type="work_order", related_id=work_order.id
            )

    @staticmethod
    def notify_replenishment(db: Session, request, part_stock, wind_farm, event_type: str):
        event_titles = {
            "created": f"【补货】{part_stock.part.name} 库存不足，申请补货",
            "approved": f"【审批】{part_stock.part.name} 补货申请已批准",
            "rejected": f"【审批】{part_stock.part.name} 补货申请已拒绝",
            "procuring": f"【采购】{part_stock.part.name} 正在采购中",
            "completed": f"【到货】{part_stock.part.name} 补货已完成",
            "delayed": f"【延期】{part_stock.part.name} 采购延期未到货"
        }
        title = event_titles.get(event_type, f"【补货】{part_stock.part.name} 状态变更")
        content = (
            f"申请编号: {request.request_code}\n"
            f"备件名称: {part_stock.part.name}\n"
            f"申请数量: {request.requested_quantity}\n"
            f"当前库存: {part_stock.quantity}\n"
            f"当前状态: {request.status.value}"
        )

        if event_type == "created":
            recipients = NotificationService._get_supervisors_and_dispatchers(db, wind_farm.id)
        elif event_type == "delayed":
            recipients = NotificationService._get_supervisors_and_dispatchers(db, wind_farm.id)
            recipients.extend(NotificationService._get_procurement_users(db))
        elif event_type == "approved" or event_type == "procuring":
            recipients = NotificationService._get_procurement_users(db)
            if request.created_by:
                recipients.append(request.created_by)
        else:
            recipients = [request.created_by] if request.created_by else []
            recipients.extend(NotificationService._get_supervisors_and_dispatchers(db, wind_farm.id))

        for user_id in list(set(recipients)):
            NotificationService.create_notification(
                db, user_id, "replenishment", title, content,
                related_type="replenishment", related_id=request.id
            )

    @staticmethod
    def notify_persistent_fault(db: Session, turbine, fault_type, count: int):
        title = f"【顽固隐患】风机 {turbine.turbine_code} 同类故障重复出现"
        content = (
            f"风机 {turbine.turbine_code} 在30天内 {fault_type.value} 类型故障已发生 {count} 次，"
            f"已标记为顽固隐患，请安排深度检查。"
        )
        recipients = NotificationService._get_all_managers(db, turbine.wind_farm_id)
        for user_id in recipients:
            NotificationService.create_notification(
                db, user_id, "persistent_fault", title, content,
                related_type="turbine", related_id=turbine.id
            )

    @staticmethod
    def notify_maintenance(db: Session, task, turbine, event_type: str):
        event_titles = {
            "created": f"【维保】风机 {turbine.turbine_code} 维保任务创建",
            "assigned": f"【维保】您有新维保任务 - 风机 {turbine.turbine_code}",
            "completed": f"【维保完成】风机 {turbine.turbine_code} 维保已完成"
        }
        title = event_titles.get(event_type, f"【维保】风机 {turbine.turbine_code} 状态变更")
        content = (
            f"任务类型: {task.task_type}\n"
            f"计划时间: {task.scheduled_date}\n"
            f"当前状态: {task.status.value}\n"
            f"{task.description or ''}"
        )

        if event_type == "assigned" and task.assignee_id:
            recipients = [task.assignee_id]
        else:
            recipients = NotificationService._get_supervisors_and_dispatchers(db, turbine.wind_farm_id)
            if task.assignee_id:
                recipients.append(task.assignee_id)

        for user_id in list(set(recipients)):
            NotificationService.create_notification(
                db, user_id, "maintenance", title, content,
                related_type="maintenance", related_id=task.id
            )

    @staticmethod
    def _get_recipients_for_warning(db: Session, wind_farm_id: int, urgency_level) -> List[int]:
        query = db.query(User.id).filter(User.is_active == True)
        if urgency_level.value in ["high", "critical"]:
            users = query.filter(
                (User.wind_farm_id == wind_farm_id) |
                (User.role.in_([UserRole.DISPATCHER, UserRole.ADMIN]))
            ).all()
        else:
            users = query.filter(
                (User.wind_farm_id == wind_farm_id) &
                (User.role.in_([UserRole.SUPERVISOR, UserRole.OPERATOR]))
            ).all()
        return [u[0] for u in users]

    @staticmethod
    def _get_recipients_for_work_order(db: Session, wind_farm_id: int, urgency_level) -> List[int]:
        return NotificationService._get_recipients_for_warning(db, wind_farm_id, urgency_level)

    @staticmethod
    def _get_recipients_for_escalation(db: Session, wind_farm_id: int, level: int) -> List[int]:
        query = db.query(User.id).filter(User.is_active == True)
        if level >= 2:
            users = query.filter(
                User.role.in_([UserRole.DISPATCHER, UserRole.ADMIN])
            ).all()
        else:
            users = query.filter(
                (User.wind_farm_id == wind_farm_id) &
                (User.role == UserRole.SUPERVISOR)
            ).all()
            users_dispatcher = query.filter(
                User.role.in_([UserRole.DISPATCHER, UserRole.ADMIN])
            ).all()
            users = list(set(users + users_dispatcher))
        return [u[0] for u in users]

    @staticmethod
    def _get_supervisors_and_dispatchers(db: Session, wind_farm_id: int) -> List[int]:
        users = db.query(User.id).filter(
            User.is_active == True,
            (
                (User.wind_farm_id == wind_farm_id) & (User.role == UserRole.SUPERVISOR)
            ) | (
                User.role.in_([UserRole.DISPATCHER, UserRole.ADMIN])
            )
        ).all()
        return [u[0] for u in users]

    @staticmethod
    def _get_all_managers(db: Session, wind_farm_id: int) -> List[int]:
        users = db.query(User.id).filter(
            User.is_active == True,
            (
                (User.wind_farm_id == wind_farm_id) &
                (User.role.in_([UserRole.SUPERVISOR, UserRole.OPERATOR]))
            ) | (
                User.role.in_([UserRole.DISPATCHER, UserRole.ADMIN])
            )
        ).all()
        return [u[0] for u in users]

    @staticmethod
    def _get_procurement_users(db: Session) -> List[int]:
        users = db.query(User.id).filter(
            User.is_active == True,
            User.role.in_([UserRole.PROCUREMENT, UserRole.ADMIN])
        ).all()
        return [u[0] for u in users]
