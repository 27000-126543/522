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
    SparePart, SparePartStock, ReplenishmentRequest, ReplenishmentLog,
    User, UserRole, StockStatus, ReplenishmentStatus, WindFarm
)
from app.schemas.schemas import (
    SparePartCreate, SparePartUpdate, SparePartResponse,
    SparePartStockResponse, ReplenishmentRequestCreate,
    ReplenishmentApproval, ReplenishmentUpdate, ReplenishmentResponse,
    ReplenishmentLogResponse
)

router = APIRouter(prefix="/api/spare-parts", tags=["备件库存管理"])


@router.post("", response_model=SparePartResponse)
async def create_spare_part(
    part_data: SparePartCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.PROCUREMENT, UserRole.SUPERVISOR
    ))
):
    existing = db.query(SparePart).filter(
        SparePart.part_code == part_data.part_code
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="备件编码已存在")

    data = part_data.model_dump()
    part = SparePart(**data)
    db.add(part)
    db.flush()

    farms = db.query(WindFarm).all()
    for farm in farms:
        existing_stock = db.query(SparePartStock).filter(
            SparePartStock.part_id == part.id,
            SparePartStock.wind_farm_id == farm.id
        ).first()
        if not existing_stock:
            SparePartService.create_stock(db, part.id, farm.id, quantity=0)

    db.commit()
    db.refresh(part)
    return part


@router.get("", response_model=List[SparePartResponse])
async def list_spare_parts(
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(SparePart)
    if category:
        query = query.filter(SparePart.category == category)
    if keyword:
        query = query.filter(
            (SparePart.name.contains(keyword)) |
            (SparePart.part_code.contains(keyword))
        )
    return query.offset(skip).limit(limit).all()


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


@router.post("/stocks/{stock_id}/adjust")
async def adjust_stock_quantity(
    stock_id: int,
    change: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.PROCUREMENT, UserRole.SUPERVISOR
    ))
):
    try:
        stock = SparePartService.update_stock_quantity(
            db, stock_id, change, check_safety=True
        )
        if not stock:
            raise HTTPException(status_code=404, detail="库存记录不存在")
        db.commit()
        return {"message": "库存调整成功", "new_quantity": stock.quantity, "status": stock.status.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/stocks/{stock_id}/safety-line")
async def set_safety_stock(
    stock_id: int,
    safety_stock: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.PROCUREMENT
    ))
):
    stock = db.query(SparePartStock).filter(SparePartStock.id == stock_id).first()
    if not stock:
        raise HTTPException(status_code=404, detail="库存记录不存在")
    stock.safety_stock = safety_stock
    stock.status = SparePartService._calculate_stock_status(stock.quantity, safety_stock)
    db.commit()
    db.refresh(stock)
    return {"message": "安全库存已更新", "safety_stock": safety_stock, "status": stock.status.value}


@router.post("/replenishment", response_model=ReplenishmentResponse)
async def create_replenishment_request(
    req_data: ReplenishmentRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.PROCUREMENT, UserRole.OPERATOR
    ))
):
    stock = db.query(SparePartStock).filter(
        SparePartStock.id == req_data.part_stock_id
    ).first()
    if not stock:
        raise HTTPException(status_code=404, detail="库存记录不存在")

    request = SparePartService.create_replenishment_request(
        db, req_data.part_stock_id, req_data.requested_quantity,
        reason=req_data.reason, created_by=current_user.id, auto=False
    )

    wind_farm = stock.wind_farm
    recipients = NotificationService._get_supervisors_and_dispatchers(
        db, stock.wind_farm_id
    )
    procurement_users = NotificationService._get_procurement_users(db)
    all_recipients = list(set(recipients + procurement_users))
    await PushNotificationService.push_replenishment(
        all_recipients, request, stock, stock.part, wind_farm, "created"
    )

    db.commit()
    db.refresh(request)
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
    return request


@router.post("/replenishment/{request_id}/approve", response_model=ReplenishmentResponse)
async def approve_replenishment(
    request_id: int,
    approval_data: ReplenishmentApproval,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR
    ))
):
    request = SparePartService.approve_request(
        db, request_id, approval_data.approved, current_user.id,
        approval_data.approval_notes
    )
    if not request:
        raise HTTPException(status_code=404, detail="补货申请不存在或已审批")

    stock = db.query(SparePartStock).filter(
        SparePartStock.id == request.part_stock_id
    ).first()
    if stock:
        wind_farm = stock.wind_farm
        event = "approved" if approval_data.approved else "rejected"
        if approval_data.approved:
            procurement_users = NotificationService._get_procurement_users(db)
            recipients = list(set(procurement_users + ([request.created_by] if request.created_by else [])))
        else:
            recipients = [request.created_by] if request.created_by else []
        await PushNotificationService.push_replenishment(
            list(set(recipients)), request, stock, stock.part, wind_farm, event
        )

    db.commit()
    db.refresh(request)
    return request


@router.put("/replenishment/{request_id}/procurement", response_model=ReplenishmentResponse)
async def update_procurement_info(
    request_id: int,
    update_data: ReplenishmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(
        UserRole.ADMIN, UserRole.PROCUREMENT
    ))
):
    request = SparePartService.update_procurement(
        db, request_id,
        procurement_order=update_data.procurement_order,
        estimated_delivery=update_data.estimated_delivery,
        actual_delivery=update_data.actual_delivery
    )
    if not request:
        raise HTTPException(status_code=404, detail="补货申请不存在")

    stock = db.query(SparePartStock).filter(
        SparePartStock.id == request.part_stock_id
    ).first()
    if stock and update_data.actual_delivery:
        wind_farm = stock.wind_farm
        recipients = [request.created_by] if request.created_by else []
        recipients.extend(
            NotificationService._get_supervisors_and_dispatchers(db, stock.wind_farm_id)
        )
        procurement_users = NotificationService._get_procurement_users(db)
        all_recipients = list(set(recipients + procurement_users))
        await PushNotificationService.push_replenishment(
            all_recipients, request, stock, stock.part, wind_farm, "completed"
        )

    db.commit()
    db.refresh(request)
    return request


@router.post("/init-demo")
async def init_demo_parts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN))
):
    import random
    demo_parts = [
        {"part_code": "SP-GB-OIL-001", "name": "齿轮箱润滑油", "category": "油品", "specification": "320#合成齿轮油",
         "unit": "桶", "price": 2800.0, "supplier": "壳牌", "lead_time_days": 3,
         "suitable_models": ["GW155-4.5MW", "SE146-3.6MW", "MY-5.0MW", "GW171-6.0MW"]},
        {"part_code": "SP-MF-BEAR-001", "name": "主轴承", "category": "机械", "specification": "SKF 240/850",
         "unit": "套", "price": 185000.0, "supplier": "SKF", "lead_time_days": 30,
         "suitable_models": ["GW155-4.5MW", "GW171-6.0MW"]},
        {"part_code": "SP-HD-PUMP-001", "name": "液压泵", "category": "液压", "specification": "柱塞泵 28cc/rev",
         "unit": "台", "price": 28500.0, "supplier": "Rexroth", "lead_time_days": 15,
         "suitable_models": ["GW155-4.5MW", "SE146-3.6MW", "MY-5.0MW"]},
        {"part_code": "SP-EL-CONV-001", "name": "变频器IGBT模块", "category": "电气", "specification": "690V 1500A",
         "unit": "块", "price": 42000.0, "supplier": "ABB", "lead_time_days": 20,
         "suitable_models": ["GW155-4.5MW", "SE146-3.6MW", "GW171-6.0MW"]},
        {"part_code": "SP-BL-SEAL-001", "name": "叶片密封组件", "category": "叶片", "specification": "成套密封",
         "unit": "套", "price": 15800.0, "supplier": "中材科技", "lead_time_days": 10,
         "suitable_models": ["GW155-4.5MW", "SE146-3.6MW", "MY-5.0MW", "GW171-6.0MW"]},
        {"part_code": "SP-GN-COOL-001", "name": "发电机冷却风扇", "category": "发电机", "specification": "500mm 轴流风扇",
         "unit": "台", "price": 6800.0, "supplier": "卧龙电气", "lead_time_days": 7,
         "suitable_models": ["GW155-4.5MW", "SE146-3.6MW", "MY-5.0MW", "GW171-6.0MW"]},
    ]

    created_parts = []
    farms = db.query(WindFarm).all()

    for dp in demo_parts:
        existing = db.query(SparePart).filter(SparePart.part_code == dp["part_code"]).first()
        if not existing:
            part = SparePart(**dp)
            db.add(part)
            db.flush()
            for farm in farms:
                es = db.query(SparePartStock).filter(
                    SparePartStock.part_id == part.id,
                    SparePartStock.wind_farm_id == farm.id
                ).first()
                if not es:
                    qty = random.randint(0, 25)
                    safety = random.randint(5, 15)
                    SparePartService.create_stock(db, part.id, farm.id, qty, safety)
            created_parts.append(dp["name"])

    db.commit()
    return {"message": "演示备件初始化完成", "created": created_parts}


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
        UserRole.ADMIN, UserRole.PROCUREMENT, UserRole.SUPERVISOR
    ))
):
    part = db.query(SparePart).filter(SparePart.id == part_id).first()
    if not part:
        raise HTTPException(status_code=404, detail="备件不存在")

    for key, value in part_data.model_dump(exclude_unset=True).items():
        setattr(part, key, value)
    db.commit()
    db.refresh(part)
    return part
