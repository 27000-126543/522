from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth import get_current_user, require_roles
from app.models.models import WindFarm, Turbine, User, UserRole
from app.schemas.schemas import (
    WindFarmCreate, WindFarmUpdate, WindFarmResponse,
    TurbineCreate, TurbineUpdate, TurbineResponse
)

router = APIRouter(prefix="/api/farms", tags=["风电场与风机管理"])


@router.post("", response_model=WindFarmResponse)
async def create_wind_farm(
    farm_data: WindFarmCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))
):
    existing = db.query(WindFarm).filter(WindFarm.name == farm_data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="风电场名称已存在")
    farm = WindFarm(**farm_data.model_dump())
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return farm


@router.get("", response_model=List[WindFarmResponse])
async def list_wind_farms(
    region: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(WindFarm)
    if region:
        query = query.filter(WindFarm.region.contains(region))
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id:
            query = query.filter(WindFarm.id == current_user.wind_farm_id)
    return query.offset(skip).limit(limit).all()


@router.get("/turbines/{turbine_id}", response_model=TurbineResponse)
async def get_turbine(
    turbine_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    turbine = db.query(Turbine).filter(Turbine.id == turbine_id).first()
    if not turbine:
        raise HTTPException(status_code=404, detail="风机不存在")
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id and current_user.wind_farm_id != turbine.wind_farm_id:
            raise HTTPException(status_code=403, detail="无权限访问该风机")
    return turbine


@router.put("/turbines/{turbine_id}", response_model=TurbineResponse)
async def update_turbine(
    turbine_id: int,
    turbine_data: TurbineUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR))
):
    turbine = db.query(Turbine).filter(Turbine.id == turbine_id).first()
    if not turbine:
        raise HTTPException(status_code=404, detail="风机不存在")
    for key, value in turbine_data.model_dump(exclude_unset=True).items():
        setattr(turbine, key, value)
    db.commit()
    db.refresh(turbine)
    return turbine


@router.post("/init-demo")
async def init_demo_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN))
):
    from datetime import datetime
    farm1 = db.query(WindFarm).filter(WindFarm.name == "华北第一风电场").first()
    if not farm1:
        farm1 = WindFarm(
            name="华北第一风电场",
            location="河北省张家口市",
            region="华北",
            capacity_mw=300,
            supervisor_id=3
        )
        db.add(farm1)
        db.flush()

        turbine_models = ["GW155-4.5MW", "GW155-4.5MW", "SE146-3.6MW", "SE146-3.6MW", "MY-5.0MW"]
        for i in range(1, 21):
            model = turbine_models[i % len(turbine_models)]
            turbine = Turbine(
                turbine_code=f"WF01-T{i:03d}",
                model=model,
                wind_farm_id=farm1.id,
                location_lat=40.8 + i * 0.01,
                location_lng=114.8 + i * 0.015,
                capacity_kw=4500 if "4.5" in model else 3600 if "3.6" in model else 5000,
                installation_date=datetime(2021, 6, 1)
            )
            db.add(turbine)

    farm2 = db.query(WindFarm).filter(WindFarm.name == "西北戈壁风电场").first()
    if not farm2:
        farm2 = WindFarm(
            name="西北戈壁风电场",
            location="甘肃省酒泉市",
            region="西北",
            capacity_mw=500
        )
        db.add(farm2)
        db.flush()

        for i in range(1, 31):
            turbine = Turbine(
                turbine_code=f"WF02-T{i:03d}",
                model="GW171-6.0MW",
                wind_farm_id=farm2.id,
                location_lat=40.2 + i * 0.008,
                location_lng=97.0 + i * 0.02,
                capacity_kw=6000,
                installation_date=datetime(2022, 3, 15)
            )
            db.add(turbine)

    db.commit()
    return {"message": "演示数据初始化完成"}


@router.get("/{farm_id}", response_model=WindFarmResponse)
async def get_wind_farm(
    farm_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    farm = db.query(WindFarm).filter(WindFarm.id == farm_id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="风电场不存在")
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id and current_user.wind_farm_id != farm_id:
            raise HTTPException(status_code=403, detail="无权限访问该风电场")
    return farm


@router.put("/{farm_id}", response_model=WindFarmResponse)
async def update_wind_farm(
    farm_id: int,
    farm_data: WindFarmUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))
):
    farm = db.query(WindFarm).filter(WindFarm.id == farm_id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="风电场不存在")
    for key, value in farm_data.model_dump(exclude_unset=True).items():
        setattr(farm, key, value)
    db.commit()
    db.refresh(farm)
    return farm


@router.post("/{farm_id}/turbines", response_model=TurbineResponse)
async def create_turbine(
    farm_id: int,
    turbine_data: TurbineCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR))
):
    farm = db.query(WindFarm).filter(WindFarm.id == farm_id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="风电场不存在")
    existing = db.query(Turbine).filter(Turbine.turbine_code == turbine_data.turbine_code).first()
    if existing:
        raise HTTPException(status_code=400, detail="风机编号已存在")
    turbine = Turbine(**turbine_data.model_dump())
    turbine.wind_farm_id = farm_id
    db.add(turbine)
    db.commit()
    db.refresh(turbine)
    return turbine


@router.get("/{farm_id}/turbines", response_model=List[TurbineResponse])
async def list_turbines(
    farm_id: int,
    model: Optional[str] = None,
    health_status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(Turbine).filter(Turbine.wind_farm_id == farm_id)
    if model:
        query = query.filter(Turbine.model.contains(model))
    if health_status:
        query = query.filter(Turbine.health_status == health_status)
    if current_user.role in [UserRole.SUPERVISOR, UserRole.OPERATOR]:
        if current_user.wind_farm_id and current_user.wind_farm_id != farm_id:
            raise HTTPException(status_code=403, detail="无权限访问该风电场风机")
    return query.offset(skip).limit(limit).all()
