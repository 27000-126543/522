from typing import Dict, Set, Optional
from fastapi import WebSocket
import json
from datetime import datetime


class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[int, Set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_personal_message(self, user_id: int, message: dict):
        if user_id in self.active_connections:
            connections = list(self.active_connections[user_id])
            dead_connections = []
            for conn in connections:
                try:
                    await conn.send_text(json.dumps(message, ensure_ascii=False, default=str))
                except Exception:
                    dead_connections.append(conn)
            for dead in dead_connections:
                self.disconnect(user_id, dead)

    async def broadcast(self, user_ids: list, message: dict):
        for user_id in user_ids:
            await self.send_personal_message(user_id, message)

    async def broadcast_to_all(self, message: dict):
        all_ids = list(self.active_connections.keys())
        await self.broadcast(all_ids, message)

    def is_online(self, user_id: int) -> bool:
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0

    def get_online_count(self) -> int:
        return sum(len(conns) for conns in self.active_connections.values())

    def get_online_users(self) -> list:
        return list(self.active_connections.keys())


ws_manager = WebSocketManager()


class PushNotificationService:
    @staticmethod
    def _build_message(
        msg_type: str,
        title: str,
        content: str,
        related_type: Optional[str] = None,
        related_id: Optional[int] = None,
        data: Optional[dict] = None
    ) -> dict:
        return {
            "type": msg_type,
            "title": title,
            "content": content,
            "related_type": related_type,
            "related_id": related_id,
            "data": data or {},
            "timestamp": datetime.now().isoformat()
        }

    @staticmethod
    async def push_warning(user_ids: list, warning, turbine):
        msg = PushNotificationService._build_message(
            "warning",
            f"【预警】风机 {turbine.turbine_code} {warning.warning_level.value.upper()}级",
            f"故障类型: {warning.fault_type.value}, 紧急程度: {warning.urgency_level.value}",
            "warning",
            warning.id,
            {"warning_level": warning.warning_level.value,
             "turbine_code": turbine.turbine_code}
        )
        await ws_manager.broadcast(user_ids, msg)

    @staticmethod
    async def push_work_order(user_ids: list, work_order, turbine, event_type: str):
        event_titles = {
            "created": "新工单创建",
            "assigned": "您有新工单待处理",
            "escalated": "工单已升级",
            "completed": "工单已完成"
        }
        title = event_titles.get(event_type, "工单状态变更")
        msg = PushNotificationService._build_message(
            "work_order",
            f"【{title}】风机 {turbine.turbine_code}",
            f"工单#{work_order.order_code}: {work_order.fault_type.value}, "
            f"状态: {work_order.status.value}",
            "work_order",
            work_order.id,
            {"order_code": work_order.order_code,
             "status": work_order.status.value,
             "event_type": event_type}
        )
        await ws_manager.broadcast(user_ids, msg)

    @staticmethod
    async def push_replenishment(user_ids: list, request, stock, part, wind_farm, event_type: str):
        event_titles = {
            "created": "补货申请创建",
            "approved": "补货申请已批准",
            "rejected": "补货申请已拒绝",
            "procuring": "补货采购中",
            "completed": "补货已到货"
        }
        title = event_titles.get(event_type, "补货状态变更")
        farm_name = wind_farm.name if wind_farm else "未知场站"
        current_qty = stock.quantity if stock else 0
        safety_stock = stock.safety_stock if stock else 0

        content = (
            f"场站: {farm_name}\n"
            f"备件: {part.name}\n"
            f"申请数量: {request.requested_quantity}{part.unit or '件'}\n"
            f"当前库存: {current_qty}, 安全线: {safety_stock}\n"
            f"状态: {request.status.value}"
        )
        msg = PushNotificationService._build_message(
            "replenishment",
            f"【{title}】{part.name}",
            content,
            "replenishment",
            request.id,
            {
                "request_code": request.request_code,
                "part_name": part.name,
                "part_code": part.part_code,
                "wind_farm_id": wind_farm.id if wind_farm else None,
                "wind_farm_name": farm_name,
                "current_quantity": current_qty,
                "safety_stock": safety_stock,
                "requested_quantity": request.requested_quantity,
                "source": request.source if hasattr(request, 'source') else None,
                "event_type": event_type
            }
        )
        await ws_manager.broadcast(user_ids, msg)

    @staticmethod
    async def push_maintenance(user_ids: list, task, turbine, event_type: str):
        event_titles = {
            "created": "维保任务创建",
            "assigned": "您有新维保任务",
            "completed": "维保任务已完成"
        }
        title = event_titles.get(event_type, "维保任务变更")
        msg = PushNotificationService._build_message(
            "maintenance",
            f"【{title}】风机 {turbine.turbine_code}",
            f"{task.task_type}, 计划: {task.scheduled_date}, "
            f"状态: {task.status.value}",
            "maintenance",
            task.id,
            {"task_type": task.task_type,
             "scheduled_date": task.scheduled_date.isoformat() if task.scheduled_date else None}
        )
        await ws_manager.broadcast(user_ids, msg)
