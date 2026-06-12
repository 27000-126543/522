from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth import get_current_user, require_roles
from app.services.spare_part import SparePartService
from app.services.notification import NotificationService
from app.services.websocket_push import PushNotificationService
from app.models.models import (
    SparePart, SparePartStock, ReplenishmentRequest, StockTransaction,
    User, UserRole, StockStatus, ReplenishmentStatus, WindFarm
)
from app.schemas.schemas import (
    SparePartCreate, SparePartUpdate, SparePartResponse,
    SparePartStockResponse, SparePartStockDetailResponse,
    StockTransactionResponse,
    ReplenishmentRequestCreate,
    ReplenishmentApproval, ReplenishmentUpdate, ReplenishmentResponse,
    ReplenishmentDeliveryReceive,
    ReplenishmentLogResponse
)

router = APIRouter(prefix="/api/spare-parts", tags=["spare-parts"])


@router.post("", response_model=SparePartResponse)
async def create_spare_part(
    part_data: SparePartCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.PROCUREMENT
    )),
):
    existing = db.query(SparePart).filter(
        SparePart.part_code == part_data.part_code
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="备件编码已存在")

    part = SparePart(**part_data.model_dump())
    db.add(part)
    db.flush()

    farms = db.query(WindFarm).all()
    for farm in farms:
        stock = SparePartService.create_stock(
            db, part.id, farm.id,
            quantity=0,
            safety_stock=part_data.safety_stock or 10
        )
        db.add(stock)

    db.commit()
    db.refresh(part)
    return part


@router.get("", response_model=List[SparePartResponse])
async def list_spare_parts(
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(SparePart)
    if category:
        query = query.filter(SparePart.category == category)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (SparePart.part_code.like(like))
            | (SparePart.name.like(like))
            | (SparePart.specification.like(like))
        )
    results = query.offset(skip).limit(limit).all()
    return results if results is not None else []


@router.post("/stocks/list", response_model=List[SparePartStockResponse])
@router.get("/stocks/list", response_model=List[SparePartStockResponse])
async def list_stocks(
    wind_farm_id: Optional[int] = None,
    status: Optional[StockStatus] = None,
    below_safety_only: bool = False,
    below_safety_by_available: bool = False,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(SparePartStock)
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            query = query.filter(SparePartStock.wind_farm_id == current_user.wind_farm_id)
    if wind_farm_id:
        query = query.filter(SparePartStock.wind_farm_id == wind_farm_id)
    if status:
        query = query.filter(SparePartStock.status == status)
    if below_safety_only:
        query = query.filter(SparePartStock.quantity < SparePartStock.safety_stock)

    results = query.offset(skip).limit(limit).all()

    if below_safety_by_available:
        results = [
            s for s in results
            if (s.quantity - s.reserved_quantity) < s.safety_stock
        ]

    for s in results:
        s.available_quantity = max(0, s.quantity - s.reserved_quantity)

    return results if results is not None else []


@router.get("/stocks/{stock_id}", response_model=SparePartStockDetailResponse)
async def get_stock_detail(
    stock_id: int,
    recent_limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stock = db.query(SparePartStock).filter(
        SparePartStock.id == stock_id
    ).first()
    if not stock:
        raise HTTPException(status_code=404, detail="库存记录不存在")

    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id and stock.wind_farm_id != current_user.wind_farm_id:
            raise HTTPException(status_code=403, detail="无权限访问该场站库存")

    stock.available_quantity = max(0, stock.quantity - stock.reserved_quantity)

    pending_qty = 0
    pending_reqs = db.query(ReplenishmentRequest).filter(
        ReplenishmentRequest.part_stock_id == stock_id,
        ReplenishmentRequest.status.in_([
            ReplenishmentStatus.PENDING,
            ReplenishmentStatus.APPROVED,
            ReplenishmentStatus.PROCURING
        ])
    ).all()
    for req in pending_reqs:
        remaining = req.requested_quantity - req.total_received
        pending_qty += remaining

    recent_txns = db.query(StockTransaction).filter(
        StockTransaction.stock_id == stock_id
    ).order_by(StockTransaction.created_at.desc()).limit(recent_limit).all()

    return SparePartStockDetailResponse(
        id=stock.id,
        part_id=stock.part_id,
        part=stock.part,
        wind_farm_id=stock.wind_farm_id,
        quantity=stock.quantity,
        safety_stock=stock.safety_stock,
        reserved_quantity=stock.reserved_quantity,
        available_quantity=stock.available_quantity,
        pending_quantity=pending_qty,
        status=stock.status,
        recent_transactions=recent_txns,
    )


@router.get("/transactions", response_model=List[StockTransactionResponse])
async def list_stock_transactions(
    wind_farm_id: Optional[int] = None,
    stock_id: Optional[int] = None,
    part_id: Optional[int] = None,
    trans_type: Optional[str] = None,
    source_type: Optional[str] = None,
    source_code: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(StockTransaction).join(
        SparePartStock, StockTransaction.stock_id == SparePartStock.id
    )

    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            query = query.filter(SparePartStock.wind_farm_id == current_user.wind_farm_id)
    if wind_farm_id:
        query = query.filter(SparePartStock.wind_farm_id == wind_farm_id)
    if stock_id:
        query = query.filter(StockTransaction.stock_id == stock_id)
    if part_id:
        query = query.filter(SparePartStock.part_id == part_id)
    if trans_type:
        query = query.filter(StockTransaction.trans_type == trans_type)
    if source_type:
        query = query.filter(StockTransaction.source_type == source_type)
    if source_code:
        like = f"%{source_code}%"
        query = query.filter(StockTransaction.source_code.like(like))
    if start_time:
        query = query.filter(StockTransaction.created_at >= start_time)
    if end_time:
        query = query.filter(StockTransaction.created_at <= end_time)

    results = query.order_by(StockTransaction.created_at.desc()).offset(skip).limit(limit).all()
    return results if results is not None else []


@router.post("/stocks/{stock_id}/adjust", response_model=SparePartStockResponse)
async def adjust_stock(
    stock_id: int,
    change: int,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    )),
):
    try:
        operator_name = current_user.full_name
        stock = SparePartService.update_stock_quantity(
            db, stock_id, change, check_safety=True, respect_lock=False,
            trans_type="manual_adjust",
            operator_id=current_user.id,
            operator_name=operator_name,
            remarks=reason,
        )
        if not stock:
            raise HTTPException(status_code=404, detail="库存记录不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    db.refresh(stock)
    stock.available_quantity = max(0, stock.quantity - stock.reserved_quantity)
    return stock


@router.post("/stocks/{stock_id}/safety-line", response_model=SparePartStockResponse)
async def update_safety_stock(
    stock_id: int,
    safety_stock: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    )),
):
    stock = db.query(SparePartStock).filter(
        SparePartStock.id == stock_id
    ).first()
    if not stock:
        raise HTTPException(status_code=404, detail="库存记录不存在")

    if safety_stock < 0:
        raise HTTPException(status_code=400, detail="安全库存不能为负数")

    stock.safety_stock = safety_stock
    stock.status = SparePartService._calculate_stock_status(stock.quantity, safety_stock)
    stock.last_updated = datetime.now()
    db.commit()
    db.refresh(stock)
    stock.available_quantity = max(0, stock.quantity - stock.reserved_quantity)
    return stock


@router.post("/replenishment", response_model=ReplenishmentResponse)
async def create_replenishment_request(
    req_data: ReplenishmentRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stock = db.query(SparePartStock).filter(
        SparePartStock.id == req_data.part_stock_id
    ).first()
    if not stock:
        raise HTTPException(status_code=404, detail="备件库存不存在")

    if req_data.requested_quantity <= 0:
        raise HTTPException(status_code=400, detail="申请数量必须大于0")

    # 手工创建补货：push_notification=False，router 层统一推送避免重复
    request = SparePartService.create_replenishment_request(
        db, req_data.part_stock_id, req_data.requested_quantity,
        reason=req_data.reason, created_by=current_user.id, auto=False,
        push_notification=False
    )

    # 手工补货在这里统一推送一次（唯一一次）
    wind_farm = stock.wind_farm
    recipients = list(set(
        NotificationService._get_supervisors_and_dispatchers(db, stock.wind_farm_id)
        + NotificationService._get_procurement_users(db)
    ))
    await PushNotificationService.push_replenishment(
        recipients, request, stock, stock.part, wind_farm, "created"
    )

    db.commit()
    db.refresh(request)

    remaining = request.requested_quantity - request.total_received
    request.remaining_quantity = remaining
    return request


@router.get("/replenishment/list", response_model=List[ReplenishmentResponse])
async def list_replenishment_requests(
    status: Optional[ReplenishmentStatus] = None,
    wind_farm_id: Optional[int] = None,
    source: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    mine_only: bool = False,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(ReplenishmentRequest)
    if mine_only:
        query = query.filter(ReplenishmentRequest.created_by == current_user.id)
    if status:
        query = query.filter(ReplenishmentRequest.status == status)
    if source:
        query = query.filter(ReplenishmentRequest.source == source)
    if start_time:
        query = query.filter(ReplenishmentRequest.created_at >= start_time)
    if end_time:
        query = query.filter(ReplenishmentRequest.created_at <= end_time)
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            stock_ids = [s.id for s in db.query(SparePartStock.id).filter(
                SparePartStock.wind_farm_id == current_user.wind_farm_id
            ).all()]
            if stock_ids:
                query = query.filter(ReplenishmentRequest.part_stock_id.in_(stock_ids))
            else:
                return []
    if wind_farm_id:
        stock_ids = [s.id for s in db.query(SparePartStock.id).filter(
            SparePartStock.wind_farm_id == wind_farm_id
        ).all()]
        if stock_ids:
            query = query.filter(ReplenishmentRequest.part_stock_id.in_(stock_ids))
        else:
            return []

    results = query.order_by(ReplenishmentRequest.created_at.desc()).offset(skip).limit(limit).all()

    for req in results:
        req.remaining_quantity = req.requested_quantity - req.total_received

    return results if results is not None else []


@router.get("/replenishment/{request_id}", response_model=ReplenishmentResponse)
async def get_replenishment_detail(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    request = db.query(ReplenishmentRequest).filter(
        ReplenishmentRequest.id == request_id
    ).first()
    if not request:
        raise HTTPException(status_code=404, detail="补货申请不存在")
    request.remaining_quantity = request.requested_quantity - request.total_received
    return request


@router.post("/replenishment/{request_id}/approve", response_model=ReplenishmentResponse)
async def approve_replenishment(
    request_id: int,
    approval_data: ReplenishmentApproval,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.SUPERVISOR
    )),
):
    request = SparePartService.approve_request(
        db, request_id, approval_data.approved,
        approved_by=current_user.id,
        approval_notes=approval_data.approval_notes
    )
    if not request:
        raise HTTPException(status_code=404, detail="补货申请不存在或状态非待审批")

    stock = db.query(SparePartStock).filter(
        SparePartStock.id == request.part_stock_id
    ).first()
    if stock:
        wind_farm = stock.wind_farm
        event = "approved" if approval_data.approved else "rejected"
        if approval_data.approved:
            procurement_users = NotificationService._get_procurement_users(db)
            recipients = list(set(
                procurement_users
                + ([request.created_by] if request.created_by else [])
            ))
        else:
            recipients = [request.created_by] if request.created_by else []
        await PushNotificationService.push_replenishment(
            list(set(recipients)), request, stock, stock.part, wind_farm, event
        )

    db.commit()
    db.refresh(request)
    request.remaining_quantity = request.requested_quantity - request.total_received
    return request


@router.put("/replenishment/{request_id}/procurement", response_model=ReplenishmentResponse)
async def update_replenishment_procurement(
    request_id: int,
    update_data: ReplenishmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.PROCUREMENT, UserRole.DISPATCHER
    )),
):
    request = SparePartService.update_procurement(
        db, request_id,
        procurement_order=update_data.procurement_order,
        supplier=update_data.supplier,
        estimated_delivery=update_data.estimated_delivery,
        operator_id=current_user.id,
    )
    if not request:
        raise HTTPException(status_code=404, detail="补货申请不存在")

    if update_data.actual_delivery:
        # old deprecated path: actual_delivery 走 receive-delivery 接口
        pass

    stock = db.query(SparePartStock).filter(
        SparePartStock.id == request.part_stock_id
    ).first()
    if stock and update_data.procurement_order and request.status == ReplenishmentStatus.PROCURING:
        wind_farm = stock.wind_farm
        recipients = [request.created_by] if request.created_by else []
        recipients.extend(
            NotificationService._get_supervisors_and_dispatchers(db, stock.wind_farm_id)
        )
        await PushNotificationService.push_replenishment(
            list(set(recipients)), request, stock, stock.part, wind_farm, "procuring"
        )

    db.commit()
    db.refresh(request)
    request.remaining_quantity = request.requested_quantity - request.total_received
    return request


@router.post("/replenishment/{request_id}/receive", response_model=ReplenishmentResponse)
async def receive_replenishment_delivery(
    request_id: int,
    delivery_data: ReplenishmentDeliveryReceive,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.PROCUREMENT, UserRole.DISPATCHER
    )),
):
    try:
        request = SparePartService.receive_delivery(
            db, request_id,
            quantity=delivery_data.quantity,
            batch_no=delivery_data.batch_no,
            delivery_date=delivery_data.delivery_date,
            remarks=delivery_data.remarks,
            operator_id=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not request:
        raise HTTPException(status_code=404, detail="补货申请不存在")

    stock = db.query(SparePartStock).filter(
        SparePartStock.id == request.part_stock_id
    ).first()
    if stock and delivery_data.quantity > 0:
        wind_farm = stock.wind_farm
        recipients = [request.created_by] if request.created_by else []
        recipients.extend(
            NotificationService._get_supervisors_and_dispatchers(db, stock.wind_farm_id)
        )
        recipients.extend(NotificationService._get_procurement_users(db))
        event = "completed" if request.status == ReplenishmentStatus.COMPLETED else "procuring"
        await PushNotificationService.push_replenishment(
            list(set(recipients)), request, stock, stock.part, wind_farm, event
        )

    db.commit()
    db.refresh(request)
    request.remaining_quantity = request.requested_quantity - request.total_received
    return request


@router.get("/{part_id}", response_model=SparePartResponse)
async def get_spare_part(
    part_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    part = db.query(SparePart).filter(SparePart.id == part_id).first()
    if not part:
        raise HTTPException(status_code=404, detail="备件不存在")
    return part


@router.put("/{part_id}", response_model=SparePartResponse)
async def update_spare_part(
    part_id: int,
    part_data: SparePartUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.PROCUREMENT
    )),
):
    part = db.query(SparePart).filter(SparePart.id == part_id).first()
    if not part:
        raise HTTPException(status_code=404, detail="备件不存在")

    update_dict = part_data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(part, key, value)

    part.updated_at = datetime.now()
    db.commit()
    db.refresh(part)
    return part


@router.post("/init-demo")
async def init_demo_parts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    existing = db.query(SparePart).count()
    if existing > 0:
        return {"message": f"已有 {existing} 个备件，跳过初始化"}

    demo_parts = [
        {"part_code": "GB-LUB-001", "name": "齿轮箱润滑油", "category": "润滑类",
         "specification": "320# 合成齿轮油", "unit": "桶", "price": 2800.00,
         "supplier": "壳牌中国", "lead_time_days": 7, "safety_stock": 15},
        {"part_code": "BRG-MAIN-001", "name": "主轴承", "category": "传动类",
         "specification": "240/710CAK30/W33", "unit": "个", "price": 185000.00,
         "supplier": "SKF", "lead_time_days": 45, "safety_stock": 2},
        {"part_code": "GEN-BRG-001", "name": "发电机轴承", "category": "传动类",
         "specification": "6334M/C3VL0241", "unit": "对", "price": 42000.00,
         "supplier": "FAG", "lead_time_days": 30, "safety_stock": 2},
        {"part_code": "HYD-OIL-001", "name": "液压油", "category": "液压类",
         "specification": "ISO VG46 抗磨液压油", "unit": "桶", "price": 1650.00,
         "supplier": "美孚", "lead_time_days": 5, "safety_stock": 20},
        {"part_code": "CTL-PLC-001", "name": "变桨控制板", "category": "电气类",
         "specification": "Siemens Simatic S7-1200", "unit": "块", "price": 28000.00,
         "supplier": "西门子", "lead_time_days": 15, "safety_stock": 3},
        {"part_code": "SNR-BLADE-001", "name": "叶片传感器组", "category": "传感类",
         "specification": "倾角+振动综合传感", "unit": "套", "price": 12500.00,
         "supplier": "邦纳", "lead_time_days": 20, "safety_stock": 5},
    ]

    farms = db.query(WindFarm).all()
    import random

    count = 0
    for pd in demo_parts:
        part = SparePart(**pd)
        db.add(part)
        db.flush()

        for farm in farms:
            ss = pd["safety_stock"]
            qty = random.randint(ss + 3, ss * 3)
            stock = SparePartService.create_stock(
                db, part.id, farm.id, quantity=qty, safety_stock=ss
            )
            db.add(stock)
            count += 1

    db.commit()
    return {"message": f"初始化 {len(demo_parts)} 个备件，共 {count} 条库存记录"}
