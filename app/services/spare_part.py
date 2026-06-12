from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.models import (
    SparePart, SparePartStock, ReplenishmentRequest,
    StockStatus, ReplenishmentStatus, WindFarm, UserRole
)
from app.services.notification import NotificationService
from app.services.websocket_push import PushNotificationService, ws_manager
import logging

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
        return stock

    @staticmethod
    def update_stock_quantity(
        db: Session,
        stock_id: int,
        change: int,
        check_safety: bool = True,
        respect_lock: bool = False
    ) -> Optional[SparePartStock]:
        stock = db.query(SparePartStock).filter(
            SparePartStock.id == stock_id
        ).first()
        if not stock:
            return None

        if respect_lock and change < 0:
            locked_qty = db.query(
                db.query(ReplenishmentRequest)
                .filter(
                    ReplenishmentRequest.part_stock_id == stock_id,
                    ReplenishmentRequest.status == ReplenishmentStatus.APPROVED,
                    ReplenishmentRequest.locked_for_outbound == True
                ).count().scalar_subquery()
            ).scalar() or 0
            available = stock.quantity - locked_qty
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
                        reason=f"自动触发: 库存低于安全线 (当前: {stock.quantity}, 安全: {stock.safety_stock})",
                        auto=True
                    )

        db.flush()
        return stock

    @staticmethod
    def _validate_parts_availability(
        db: Session,
        wind_farm_id: int,
        parts_list: List[Dict[str, Any]]
    ) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any]]:
        validated = []
        failed = []
        total_cost = 0.0

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
                failed.append({"item": str(part_code), "reason": "库存记录不存在"})
                continue

            if stock.quantity < quantity:
                failed.append({
                    "item": str(part_code),
                    "reason": f"库存不足 (现有: {stock.quantity}, 需要: {quantity})"
                })
                continue

            validated.append({
                "stock_id": stock.id,
                "part_id": stock.part_id,
                "part_name": stock.part.name if stock.part else str(part_code),
                "part_code": stock.part.part_code if stock.part else str(part_code),
                "quantity": quantity,
                "unit_price": stock.part.price if stock.part else 0
            })
            total_cost += (stock.part.price if stock.part else 0) * quantity

        return len(failed) == 0, validated, {"failed": failed, "total_cost": total_cost}

    @staticmethod
    def consume_parts_in_transaction(
        db: Session,
        wind_farm_id: int,
        parts_list: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        ok, validated, info = SparePartService._validate_parts_availability(
            db, wind_farm_id, parts_list
        )
        if not ok:
            return {
                "success": False,
                "error": "备件库存不足，未进行任何扣减",
                "details": info["failed"]
            }

        try:
            success_list = []
            total_cost = 0.0
            affected_stock_ids = []

            for v in validated:
                stock = SparePartService.update_stock_quantity(
                    db, v["stock_id"], -v["quantity"], check_safety=True
                )
                if not stock:
                    raise RuntimeError(f"扣减库存失败: stock_id={v['stock_id']}")

                success_list.append({
                    "part_id": v["part_id"],
                    "part_name": v["part_name"],
                    "part_code": v["part_code"],
                    "quantity": v["quantity"],
                    "unit_price": v["unit_price"]
                })
                total_cost += v["unit_price"] * v["quantity"]
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
    def create_replenishment_request(
        db: Session,
        part_stock_id: int,
        requested_quantity: int,
        reason: Optional[str] = None,
        created_by: Optional[int] = None,
        auto: bool = False
    ) -> ReplenishmentRequest:
        request = ReplenishmentRequest(
            request_code=SparePartService.generate_request_code(db),
            part_stock_id=part_stock_id,
            requested_quantity=requested_quantity,
            reason=reason,
            created_by=created_by,
            status=ReplenishmentStatus.PENDING,
            locked_for_outbound=False
        )
        db.add(request)
        db.flush()

        stock = db.query(SparePartStock).filter(
            SparePartStock.id == part_stock_id
        ).first()
        if stock:
            wind_farm = stock.wind_farm
            NotificationService.notify_replenishment(
                db, request, stock, wind_farm, "created"
            )

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

        request.approved_by = approved_by
        request.approved_at = datetime.now()
        request.approval_notes = approval_notes

        if approved:
            request.status = ReplenishmentStatus.APPROVED
            request.locked_for_outbound = True

            stock = db.query(SparePartStock).filter(
                SparePartStock.id == request.part_stock_id
            ).first()
            if stock:
                lock_qty = min(request.requested_quantity, stock.quantity)
                stock.reserved_quantity = stock.reserved_quantity + lock_qty
                stock.last_updated = datetime.now()
        else:
            request.status = ReplenishmentStatus.REJECTED
            request.locked_for_outbound = False

        db.flush()

        stock = db.query(SparePartStock).filter(
            SparePartStock.id == request.part_stock_id
        ).first()
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
        estimated_delivery: Optional[datetime] = None,
        actual_delivery: Optional[datetime] = None
    ) -> Optional[ReplenishmentRequest]:
        request = db.query(ReplenishmentRequest).filter(
            ReplenishmentRequest.id == request_id
        ).first()
        if not request:
            return None

        if procurement_order:
            request.procurement_order = procurement_order
            if request.status in [ReplenishmentStatus.APPROVED, ReplenishmentStatus.PENDING]:
                request.status = ReplenishmentStatus.PROCURING

        if estimated_delivery:
            request.estimated_delivery = estimated_delivery

        if actual_delivery:
            request.actual_delivery = actual_delivery
            request.status = ReplenishmentStatus.COMPLETED

            stock = db.query(SparePartStock).filter(
                SparePartStock.id == request.part_stock_id
            ).first()
            if stock:
                release_qty = min(request.requested_quantity, stock.reserved_quantity)
                stock.reserved_quantity = max(0, stock.reserved_quantity - release_qty)
                stock.quantity += request.requested_quantity
                stock.status = SparePartService._calculate_stock_status(
                    stock.quantity, stock.safety_stock
                )
                stock.last_updated = datetime.now()

            request.locked_for_outbound = False

        db.flush()

        stock = db.query(SparePartStock).filter(
            SparePartStock.id == request.part_stock_id
        ).first()
        if stock and actual_delivery:
            wind_farm = stock.wind_farm
            NotificationService.notify_replenishment(
                db, request, stock, wind_farm, "completed"
            )
        elif stock and procurement_order and request.status == ReplenishmentStatus.PROCURING:
            wind_farm = stock.wind_farm
            NotificationService.notify_replenishment(
                db, request, stock, wind_farm, "procuring"
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

            available = stock.quantity - stock.reserved_quantity
            if available < quantity:
                results["failed"].append({
                    "item": str(part_code),
                    "reason": f"库存不足 (可用: {available}, 需要: {quantity})"
                })
                continue

            try:
                SparePartService.update_stock_quantity(db, stock.id, -quantity, check_safety=True)
                results["success"].append({
                    "part_id": stock.part_id,
                    "part_name": stock.part.name if stock.part else str(part_code),
                    "quantity": quantity,
                    "unit_price": stock.part.price if stock.part else 0
                })
                results["total_cost"] += (stock.part.price if stock.part else 0) * quantity
            except ValueError as e:
                results["failed"].append({"item": str(part_code), "reason": str(e)})

        return results

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
                        SparePartService.create_replenishment_request(
                            db, stock.id, suggested_qty,
                            reason=f"定时检查: 库存低于安全线 (当前: {stock.quantity}, 安全: {stock.safety_stock})",
                            auto=True
                        )
        db.flush()
        return low_stocks
