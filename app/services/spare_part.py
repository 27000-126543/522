from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.models import (
    SparePart, SparePartStock, ReplenishmentRequest, ReplenishmentLog,
    StockTransaction, StockStatus, ReplenishmentStatus, WindFarm, UserRole, User
)
from app.services.notification import NotificationService
from app.services.websocket_push import PushNotificationService
import logging
import asyncio

logger = logging.getLogger(__name__)


class SparePartService:
    @staticmethod
    def _calculate_stock_status(quantity: int, safety_stock: int) -> StockStatus:
        if quantity <= 0:
            return StockStatus.OUT_OF_STOCK
        elif quantity <= safety_stock * 0.3:
            return StockStatus.CRITICAL
        elif quantity <= safety_stock:
            return StockStatus.WARNING
        return StockStatus.NORMAL

    @staticmethod
    def _get_available_quantity(stock: SparePartStock) -> int:
        return max(0, stock.quantity - stock.reserved_quantity)

    @staticmethod
    def generate_request_code(db: Session) -> str:
        prefix = "RP" + datetime.now().strftime("%Y%m%d")
        last = db.query(ReplenishmentRequest).filter(
            ReplenishmentRequest.request_code.like(f"{prefix}%")
        ).order_by(ReplenishmentRequest.id.desc()).first()
        if last:
            try:
                seq = int(last.request_code[-3:]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1
        return f"{prefix}{seq:03d}"

    @staticmethod
    def _add_replenishment_log(
        db: Session,
        request_id: int,
        action: str,
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None,
        notes: Optional[str] = None
    ) -> ReplenishmentLog:
        log = ReplenishmentLog(
            request_id=request_id,
            action=action,
            operator_id=operator_id,
            operator_name=operator_name,
            notes=notes
        )
        db.add(log)
        db.flush()
        return log

    @staticmethod
    def _add_stock_transaction(
        db: Session,
        stock_id: int,
        trans_type: str,
        quantity_change: int = 0,
        reserved_change: int = 0,
        source_type: Optional[str] = None,
        source_id: Optional[int] = None,
        source_code: Optional[str] = None,
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None,
        remarks: Optional[str] = None,
    ) -> StockTransaction:
        stock = db.query(SparePartStock).filter(SparePartStock.id == stock_id).first()
        txn = StockTransaction(
            stock_id=stock_id,
            trans_type=trans_type,
            quantity_change=quantity_change,
            reserved_change=reserved_change,
            balance_after=stock.quantity if stock else None,
            reserved_after=stock.reserved_quantity if stock else None,
            source_type=source_type,
            source_id=source_id,
            source_code=source_code,
            operator_id=operator_id,
            operator_name=operator_name,
            remarks=remarks,
        )
        db.add(txn)
        db.flush()
        return txn

    @staticmethod
    def create_stock(
        db: Session,
        part_id: int,
        wind_farm_id: int,
        quantity: int = 0,
        safety_stock: int = 10
    ) -> SparePartStock:
        stock = SparePartStock(
            part_id=part_id,
            wind_farm_id=wind_farm_id,
            quantity=quantity,
            safety_stock=safety_stock,
            status=SparePartService._calculate_stock_status(quantity, safety_stock)
        )
        db.add(stock)
        db.flush()
        if quantity > 0:
            SparePartService._add_stock_transaction(
                db, stock.id, "init", quantity_change=quantity,
                remarks="初始化库存"
            )
        return stock

    @staticmethod
    def update_stock_quantity(
        db: Session,
        stock_id: int,
        change: int,
        check_safety: bool = True,
        respect_lock: bool = False,
        trans_type: str = "adjust",
        source_type: Optional[str] = None,
        source_id: Optional[int] = None,
        source_code: Optional[str] = None,
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None,
        remarks: Optional[str] = None,
    ) -> Optional[SparePartStock]:
        stock = db.query(SparePartStock).filter(
            SparePartStock.id == stock_id
        ).first()
        if not stock:
            return None

        if respect_lock and change < 0:
            available = SparePartService._get_available_quantity(stock)
            if available + change < 0:
                raise ValueError(f"可用库存不足，可用: {available}")

        new_quantity = stock.quantity + change
        if new_quantity < 0:
            raise ValueError("库存不足")

        stock.quantity = new_quantity
        stock.status = SparePartService._calculate_stock_status(
            new_quantity, stock.safety_stock
        )
        stock.last_updated = datetime.now()

        if change != 0:
            SparePartService._add_stock_transaction(
                db, stock_id, trans_type=trans_type,
                quantity_change=change, reserved_change=0,
                source_type=source_type, source_id=source_id, source_code=source_code,
                operator_id=operator_id, operator_name=operator_name, remarks=remarks,
            )

        if check_safety and stock.status in [StockStatus.WARNING, StockStatus.CRITICAL, StockStatus.OUT_OF_STOCK]:
            existing_req = db.query(ReplenishmentRequest).filter(
                ReplenishmentRequest.part_stock_id == stock_id,
                ReplenishmentRequest.status.in_([
                    ReplenishmentStatus.PENDING,
                    ReplenishmentStatus.APPROVED,
                    ReplenishmentStatus.PROCURING
                ])
            ).first()

            if not existing_req:
                suggested_qty = stock.safety_stock * 2 - stock.quantity
                if suggested_qty > 0:
                    SparePartService.create_replenishment_request(
                        db, stock_id, suggested_qty,
                        reason=f"库存低于安全线 (当前: {stock.quantity}, 安全: {stock.safety_stock})",
                        auto=True, push_notification=True
                    )

        db.flush()
        return stock

    @staticmethod
    def _merge_parts_by_key(parts_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for item in parts_list:
            key = str(item.get("part_id") or item.get("part_code") or item.get("part_name", ""))
            qty = int(item.get("quantity", 0))
            if key in merged:
                merged[key]["quantity"] += qty
            else:
                merged[key] = {
                    "part_id": item.get("part_id"),
                    "part_code": item.get("part_code"),
                    "part_name": item.get("part_name"),
                    "quantity": qty,
                }
        return merged

    @staticmethod
    def _validate_parts_availability(
        db: Session,
        wind_farm_id: int,
        parts_list: List[Dict[str, Any]]
    ) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any]]:
        merged = SparePartService._merge_parts_by_key(parts_list)
        validated = []
        failed = []
        total_cost = 0.0

        for key, item in merged.items():
            part_id = item.get("part_id")
            part_code = item.get("part_code") or key
            quantity = item["quantity"]

            stock = None
            if isinstance(part_id, int):
                stock = db.query(SparePartStock).filter(
                    SparePartStock.part_id == part_id,
                    SparePartStock.wind_farm_id == wind_farm_id
                ).first()
            if not stock and part_code:
                part = db.query(SparePart).filter(
                    SparePart.part_code == str(part_code)
                ).first()
                if part:
                    stock = db.query(SparePartStock).filter(
                        SparePartStock.part_id == part.id,
                        SparePartStock.wind_farm_id == wind_farm_id
                    ).first()

            if not stock:
                failed.append({"item": str(part_code), "reason": "库存记录不存在"})
                continue

            available = SparePartService._get_available_quantity(stock)
            if available < quantity:
                failed.append({
                    "item": str(part_code),
                    "reason": (f"可用库存不足 (可用: {available}, 总库存: {stock.quantity}, "
                               f"已锁定: {stock.reserved_quantity}, 需要: {quantity})")
                })
                continue

            unit_price = stock.part.price if stock.part else 0
            subtotal = unit_price * quantity
            validated.append({
                "stock_id": stock.id,
                "part_id": stock.part_id,
                "part_name": stock.part.name if stock.part else item.get("part_name", str(part_code)),
                "part_code": stock.part.part_code if stock.part else str(part_code),
                "quantity": quantity,
                "unit_price": unit_price,
                "subtotal": subtotal
            })
            total_cost += subtotal

        return len(failed) == 0, validated, {"failed": failed, "total_cost": total_cost}

    @staticmethod
    def consume_parts_in_transaction(
        db: Session,
        wind_farm_id: int,
        parts_list: List[Dict[str, Any]],
        work_order_id: Optional[int] = None,
        work_order_code: Optional[str] = None,
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        ok, validated, info = SparePartService._validate_parts_availability(
            db, wind_farm_id, parts_list
        )
        if not ok:
            return {
                "success": False,
                "error": "备件可用库存不足，未进行任何扣减",
                "details": info["failed"]
            }

        try:
            success_list = []
            total_cost = 0.0
            affected_stock_ids = []

            for v in validated:
                stock = SparePartService.update_stock_quantity(
                    db, v["stock_id"], -v["quantity"], check_safety=True,
                    trans_type="consume_workorder",
                    source_type="work_order", source_id=work_order_id,
                    source_code=work_order_code,
                    operator_id=operator_id, operator_name=operator_name,
                    remarks=f"工单消耗 {v['quantity']} 件",
                )
                if not stock:
                    raise RuntimeError(f"扣减库存失败: stock_id={v['stock_id']}")

                success_list.append({
                    "part_id": v["part_id"],
                    "part_name": v["part_name"],
                    "part_code": v["part_code"],
                    "quantity": v["quantity"],
                    "unit_price": v["unit_price"],
                    "subtotal": v["subtotal"]
                })
                total_cost += v["subtotal"]
                affected_stock_ids.append(v["stock_id"])

            db.flush()

            return {
                "success": True,
                "success_items": success_list,
                "total_cost": total_cost,
                "affected_stock_ids": affected_stock_ids
            }

        except Exception as e:
            logger.error(f"备件扣减事务失败: {e}")
            raise

    @staticmethod
    def _safe_push_replenishment(request, stock, part, wind_farm, event_type):
        """安全的推送 WebSocket，不阻塞主线程"""
        from app.services.websocket_push import PushNotificationService
        try:
            loop = asyncio.get_event_loop()
            recipients = list(set(
                NotificationService._get_supervisors_and_dispatchers(db=None, wind_farm_id=wind_farm.id if wind_farm else 0)
                + NotificationService._get_procurement_users(db=None)
            ))
            # 因为 db 参数在这里的接收端可能用不上，直接去全局查
            recipients_all = []
            from app.database import SessionLocal
            tmpdb = SessionLocal()
            try:
                if wind_farm:
                    recipients_all = NotificationService._get_supervisors_and_dispatchers(tmpdb, wind_farm.id)
                recipients_all.extend(NotificationService._get_procurement_users(tmpdb))
                recipients_all = list(set(recipients_all))
            finally:
                tmpdb.close()

            coro = PushNotificationService.push_replenishment(
                recipients_all, request, stock, part, wind_farm, event_type
            )
            if loop.is_running():
                asyncio.ensure_future(coro)
            else:
                loop.run_until_complete(coro)
        except Exception as e:
            logger.warning(f"推送补货WebSocket异常: {e}")

    @staticmethod
    def create_replenishment_request(
        db: Session,
        part_stock_id: int,
        requested_quantity: int,
        reason: Optional[str] = None,
        created_by: Optional[int] = None,
        auto: bool = False,
        push_notification: bool = True,
    ) -> ReplenishmentRequest:
        source = "auto" if auto else "manual"
        request = ReplenishmentRequest(
            request_code=SparePartService.generate_request_code(db),
            part_stock_id=part_stock_id,
            requested_quantity=requested_quantity,
            total_received=0,
            batch_deliveries=[],
            reason=reason,
            source=source,
            created_by=created_by,
            status=ReplenishmentStatus.PENDING,
            locked_for_outbound=False,
            delay_notified=False,
        )
        db.add(request)
        db.flush()

        creator_name = None
        if created_by:
            creator = db.query(User).filter(User.id == created_by).first()
            if creator:
                creator_name = creator.full_name

        SparePartService._add_replenishment_log(
            db, request.id,
            action="created",
            operator_id=created_by,
            operator_name=creator_name or ("系统自动" if auto else None),
            notes=reason
        )

        stock = db.query(SparePartStock).filter(
            SparePartStock.id == part_stock_id
        ).first()
        if stock:
            wind_farm = stock.wind_farm
            NotificationService.notify_replenishment(
                db, request, stock, wind_farm, "created"
            )

            if push_notification:
                # 推送 - 只在路由层调用时才需要，这里仅做站内通知；
                # router 层会再次推送，避免重复
                pass

        return request

    @staticmethod
    def approve_request(
        db: Session,
        request_id: int,
        approved: bool,
        approved_by: int,
        approval_notes: Optional[str] = None
    ) -> Optional[ReplenishmentRequest]:
        request = db.query(ReplenishmentRequest).filter(
            ReplenishmentRequest.id == request_id,
            ReplenishmentRequest.status == ReplenishmentStatus.PENDING
        ).first()
        if not request:
            return None

        approver = db.query(User).filter(User.id == approved_by).first()
        approver_name = approver.full_name if approver else None

        request.approved_by = approved_by
        request.approved_at = datetime.now()
        request.approval_notes = approval_notes

        stock = db.query(SparePartStock).filter(
            SparePartStock.id == request.part_stock_id
        ).first()

        if approved:
            request.status = ReplenishmentStatus.APPROVED
            request.locked_for_outbound = True

            if stock:
                lock_qty = min(request.requested_quantity, stock.quantity)
                old_reserved = stock.reserved_quantity
                stock.reserved_quantity = stock.reserved_quantity + lock_qty
                stock.last_updated = datetime.now()

                SparePartService._add_stock_transaction(
                    db, stock.id, trans_type="lock_replenish",
                    quantity_change=0, reserved_change=lock_qty,
                    source_type="replenishment", source_id=request.id,
                    source_code=request.request_code,
                    operator_id=approved_by, operator_name=approver_name,
                    remarks=f"补货审批通过，锁定出库 {lock_qty} (原预留: {old_reserved} -> {stock.reserved_quantity})"
                )

            SparePartService._add_replenishment_log(
                db, request.id,
                action="approved",
                operator_id=approved_by,
                operator_name=approver_name,
                notes=approval_notes or "审批通过"
            )
        else:
            request.status = ReplenishmentStatus.REJECTED
            request.locked_for_outbound = False

            SparePartService._add_replenishment_log(
                db, request.id,
                action="rejected",
                operator_id=approved_by,
                operator_name=approver_name,
                notes=approval_notes or "审批拒绝"
            )

        db.flush()

        if stock:
            wind_farm = stock.wind_farm
            event = "approved" if approved else "rejected"
            NotificationService.notify_replenishment(
                db, request, stock, wind_farm, event
            )

        return request

    @staticmethod
    def update_procurement(
        db: Session,
        request_id: int,
        procurement_order: Optional[str] = None,
        supplier: Optional[str] = None,
        estimated_delivery: Optional[datetime] = None,
        operator_id: Optional[int] = None
    ) -> Optional[ReplenishmentRequest]:
        request = db.query(ReplenishmentRequest).filter(
            ReplenishmentRequest.id == request_id
        ).first()
        if not request:
            return None

        if request.status == ReplenishmentStatus.REJECTED:
            return request

        operator_name = None
        if operator_id:
            operator = db.query(User).filter(User.id == operator_id).first()
            if operator:
                operator_name = operator.full_name

        if supplier:
            request.supplier = supplier

        if procurement_order:
            request.procurement_order = procurement_order

        if estimated_delivery:
            request.estimated_delivery = estimated_delivery
            request.delay_notified = False

        if (procurement_order or supplier or estimated_delivery) and \
           request.status in [ReplenishmentStatus.APPROVED, ReplenishmentStatus.PENDING]:
            request.status = ReplenishmentStatus.PROCURING

            notes_parts = []
            if procurement_order:
                notes_parts.append(f"采购单号: {procurement_order}")
            if supplier:
                notes_parts.append(f"供应商: {supplier}")
            if estimated_delivery:
                notes_parts.append(f"预计到货: {estimated_delivery.strftime('%Y-%m-%d')}")

            SparePartService._add_replenishment_log(
                db, request_id,
                action="procuring",
                operator_id=operator_id,
                operator_name=operator_name,
                notes=", ".join(notes_parts) if notes_parts else "采购信息已更新"
            )

        db.flush()

        stock = db.query(SparePartStock).filter(
            SparePartStock.id == request.part_stock_id
        ).first()
        if stock and request.status == ReplenishmentStatus.PROCURING and (procurement_order or supplier or estimated_delivery):
            wind_farm = stock.wind_farm
            NotificationService.notify_replenishment(
                db, request, stock, wind_farm, "procuring"
            )

        return request

    @staticmethod
    def receive_delivery(
        db: Session,
        request_id: int,
        quantity: int,
        batch_no: Optional[str] = None,
        delivery_date: Optional[datetime] = None,
        remarks: Optional[str] = None,
        operator_id: Optional[int] = None,
    ) -> Optional[ReplenishmentRequest]:
        request = db.query(ReplenishmentRequest).filter(
            ReplenishmentRequest.id == request_id
        ).first()
        if not request:
            return None
        if request.status == ReplenishmentStatus.REJECTED:
            return request
        if request.status == ReplenishmentStatus.COMPLETED:
            return request

        stock = db.query(SparePartStock).filter(
            SparePartStock.id == request.part_stock_id
        ).first()
        if not stock:
            return None

        if quantity <= 0:
            raise ValueError("到货数量必须大于0")

        remaining = request.requested_quantity - request.total_received
        actual_qty = min(quantity, remaining)
        if actual_qty <= 0:
            return request

        operator_name = None
        if operator_id:
            operator = db.query(User).filter(User.id == operator_id).first()
            if operator:
                operator_name = operator.full_name

        request.total_received += actual_qty

        # 分批记录
        batches = request.batch_deliveries or []
        batch_record = {
            "batch_no": batch_no,
            "quantity": actual_qty,
            "delivery_date": (delivery_date or datetime.now()).isoformat(),
            "remarks": remarks,
            "operator_id": operator_id,
            "operator_name": operator_name,
        }
        batches.append(batch_record)
        request.batch_deliveries = batches

        # 释放锁定 + 入库
        release_qty = min(actual_qty, stock.reserved_quantity)
        old_reserved = stock.reserved_quantity
        if release_qty > 0:
            stock.reserved_quantity = max(0, stock.reserved_quantity - release_qty)

        stock.quantity += actual_qty
        stock.status = SparePartService._calculate_stock_status(stock.quantity, stock.safety_stock)
        stock.last_updated = datetime.now()

        SparePartService._add_stock_transaction(
            db, stock.id, trans_type="receive_replenish",
            quantity_change=actual_qty,
            reserved_change=-release_qty if release_qty else 0,
            source_type="replenishment", source_id=request.id,
            source_code=request.request_code,
            operator_id=operator_id, operator_name=operator_name,
            remarks=(f"批次到货 {actual_qty}, 释放锁定 {release_qty} "
                     f"(原总库存: {stock.quantity - actual_qty}, 锁定: {old_reserved} -> {stock.reserved_quantity})"),
        )

        # 全部到齐 -> 完成
        if request.total_received >= request.requested_quantity:
            request.status = ReplenishmentStatus.COMPLETED
            request.actual_delivery = delivery_date or datetime.now()
            request.locked_for_outbound = False

            # 保险：如果还有没释放完的锁定，兜底释放
            if stock.reserved_quantity > 0:
                diff = stock.reserved_quantity
                stock.reserved_quantity = 0
                SparePartService._add_stock_transaction(
                    db, stock.id, trans_type="unlock_remaining",
                    reserved_change=-diff,
                    source_type="replenishment", source_id=request.id,
                    source_code=request.request_code,
                    remarks=f"补货完成，兜底释放剩余锁定 {diff}"
                )

        db.flush()

        SparePartService._add_replenishment_log(
            db, request_id,
            action="delivery_batch",
            operator_id=operator_id,
            operator_name=operator_name,
            notes=(f"批次到货 {actual_qty}/{request.requested_quantity} "
                   f"(累计 {request.total_received})")
        )

        if request.status == ReplenishmentStatus.COMPLETED:
            SparePartService._add_replenishment_log(
                db, request_id,
                action="completed",
                operator_id=operator_id,
                operator_name=operator_name,
                notes=f"全部到货完成, 累计数量: {request.total_received}"
            )

        wind_farm = stock.wind_farm
        event = "completed" if request.status == ReplenishmentStatus.COMPLETED else "procuring"
        NotificationService.notify_replenishment(
            db, request, stock, wind_farm, event
        )

        return request

    @staticmethod
    def consume_parts(
        db: Session,
        wind_farm_id: int,
        parts_list: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        results = {"success": [], "failed": [], "total_cost": 0.0}
        for item in parts_list:
            part_code = item.get("part_code") or item.get("part_id")
            quantity = int(item.get("quantity", 1))

            stock = None
            if isinstance(part_code, int):
                stock = db.query(SparePartStock).filter(
                    SparePartStock.part_id == part_code,
                    SparePartStock.wind_farm_id == wind_farm_id
                ).first()
            else:
                part = db.query(SparePart).filter(
                    SparePart.part_code == part_code
                ).first()
                if part:
                    stock = db.query(SparePartStock).filter(
                        SparePartStock.part_id == part.id,
                        SparePartStock.wind_farm_id == wind_farm_id
                    ).first()

            if not stock:
                results["failed"].append({"item": str(part_code), "reason": "库存不存在"})
                continue

            available = SparePartService._get_available_quantity(stock)
            if available < quantity:
                results["failed"].append({
                    "item": str(part_code),
                    "reason": f"可用库存不足 (可用: {available}, 需要: {quantity})"
                })
                continue

            try:
                SparePartService.update_stock_quantity(db, stock.id, -quantity, check_safety=True)
                unit_price = stock.part.price if stock.part else 0
                results["success"].append({
                    "part_id": stock.part_id,
                    "part_name": stock.part.name if stock.part else str(part_code),
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "subtotal": unit_price * quantity
                })
                results["total_cost"] += unit_price * quantity
            except ValueError as e:
                results["failed"].append({"item": str(part_code), "reason": str(e)})

        return results

    @staticmethod
    def check_delayed_deliveries(db: Session) -> List[ReplenishmentRequest]:
        now = datetime.now()
        delayed = db.query(ReplenishmentRequest).filter(
            ReplenishmentRequest.status == ReplenishmentStatus.PROCURING,
            ReplenishmentRequest.estimated_delivery < now,
            ReplenishmentRequest.delay_notified == False,
        ).all()

        for req in delayed:
            req.delay_notified = True
            stock = db.query(SparePartStock).filter(
                SparePartStock.id == req.part_stock_id
            ).first()
            if stock:
                wind_farm = stock.wind_farm
                NotificationService.notify_replenishment(
                    db, req, stock, wind_farm, "delayed"
                )
                try:
                    from app.database import SessionLocal
                    tmpdb = SessionLocal()
                    recipients = []
                    try:
                        recipients = NotificationService._get_supervisors_and_dispatchers(tmpdb, wind_farm.id)
                        recipients.extend(NotificationService._get_procurement_users(tmpdb))
                        recipients = list(set(recipients))
                    finally:
                        tmpdb.close()

                    from app.services.websocket_push import PushNotificationService
                    loop = asyncio.get_event_loop()
                    coro = PushNotificationService.push_replenishment(
                        recipients, req, stock, stock.part, wind_farm, "delayed"
                    )
                    if loop.is_running():
                        asyncio.ensure_future(coro)
                    else:
                        loop.run_until_complete(coro)
                except Exception as e:
                    logger.warning(f"延期推送异常: {e}")
        db.flush()
        return delayed

    @staticmethod
    def check_all_stocks(db: Session) -> List[SparePartStock]:
        stocks = db.query(SparePartStock).all()
        low_stocks = []
        for stock in stocks:
            old_status = stock.status
            stock.status = SparePartService._calculate_stock_status(
                stock.quantity, stock.safety_stock
            )
            if stock.status != old_status and stock.status in [
                StockStatus.WARNING, StockStatus.CRITICAL, StockStatus.OUT_OF_STOCK
            ]:
                low_stocks.append(stock)

                existing_req = db.query(ReplenishmentRequest).filter(
                    ReplenishmentRequest.part_stock_id == stock.id,
                    ReplenishmentRequest.status.in_([
                        ReplenishmentStatus.PENDING,
                        ReplenishmentStatus.APPROVED,
                        ReplenishmentStatus.PROCURING
                    ])
                ).first()

                if not existing_req:
                    suggested_qty = stock.safety_stock * 2 - stock.quantity
                    if suggested_qty > 0:
                        req = SparePartService.create_replenishment_request(
                            db, stock.id, suggested_qty,
                            reason=f"定时巡检: 库存低于安全线 (当前: {stock.quantity}, 安全: {stock.safety_stock})",
                            auto=True, push_notification=False
                        )
                        # 巡检生成的补货一定要推 WebSocket
                        try:
                            from app.database import SessionLocal
                            from app.services.websocket_push import PushNotificationService
                            tmpdb = SessionLocal()
                            recipients = []
                            wind_farm = stock.wind_farm
                            try:
                                if wind_farm:
                                    recipients = NotificationService._get_supervisors_and_dispatchers(tmpdb, wind_farm.id)
                                recipients.extend(NotificationService._get_procurement_users(tmpdb))
                                recipients = list(set(recipients))
                            finally:
                                tmpdb.close()

                            loop = asyncio.get_event_loop()
                            coro = PushNotificationService.push_replenishment(
                                recipients, req, stock, stock.part, wind_farm, "created"
                            )
                            if loop.is_running():
                                asyncio.ensure_future(coro)
                            else:
                                loop.run_until_complete(coro)
                        except Exception as e:
                            logger.warning(f"巡检补货推送异常: {e}")
        db.flush()
        return low_stocks
