from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import settings
from app.services.auth import (
    authenticate_user, create_access_token,
    get_current_user, hash_password, require_roles
)
from app.models.models import User, UserRole
from app.schemas.schemas import (
    Token, UserCreate, UserUpdate, UserResponse, PaginatedResponse
)
from typing import List, Optional

router = APIRouter(prefix="/api/auth", tags=["认证与用户管理"])


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return Token(
        access_token=access_token,
        token_type="bearer",
        role=user.role,
        user_id=user.id
    )


@router.post("/register", response_model=UserResponse)
async def register(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN))
):
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在"
        )
    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        full_name=user_data.full_name,
        phone=user_data.phone,
        email=user_data.email,
        role=user_data.role,
        skills=[s.value for s in user_data.skills],
        location_lat=user_data.location_lat,
        location_lng=user_data.location_lng,
        wind_farm_id=user_data.wind_farm_id
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    role: Optional[UserRole] = None,
    wind_farm_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR))
):
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if wind_farm_id:
        query = query.filter(User.wind_farm_id == wind_farm_id)
    if current_user.role == UserRole.SUPERVISOR:
        query = query.filter((User.wind_farm_id == current_user.wind_farm_id) | (User.id == current_user.id))
    return query.offset(skip).limit(limit).all()


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR))
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if current_user.role == UserRole.SUPERVISOR and user.wind_farm_id != current_user.wind_farm_id and user.id != current_user.id:
        raise HTTPException(status_code=403, detail="无权查看该用户")
    return user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER, UserRole.SUPERVISOR))
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    update_data = user_data.model_dump(exclude_unset=True)
    if "skills" in update_data and update_data["skills"]:
        update_data["skills"] = [s.value for s in update_data["skills"]]

    for key, value in update_data.items():
        setattr(user, key, value)

    db.commit()
    db.refresh(user)
    return user


@router.post("/init-default")
async def init_default_users(db: Session = Depends(get_db)):
    users_data = [
        {"username": "admin", "password": "admin123", "full_name": "系统管理员", "role": UserRole.ADMIN},
        {"username": "dispatcher", "password": "disp123", "full_name": "总部调度员", "role": UserRole.DISPATCHER},
        {"username": "supervisor1", "password": "sup123", "full_name": "风电场主管A", "role": UserRole.SUPERVISOR, "wind_farm_id": 1},
        {"username": "operator1", "password": "op123", "full_name": "运维人员张三", "role": UserRole.OPERATOR, "wind_farm_id": 1,
         "skills": ["mechanical", "electrical", "general"]},
        {"username": "operator2", "password": "op123", "full_name": "运维人员李四", "role": UserRole.OPERATOR, "wind_farm_id": 1,
         "skills": ["hydraulic", "blade", "general"]},
        {"username": "procurement", "password": "proc123", "full_name": "采购员小王", "role": UserRole.PROCUREMENT},
    ]
    created = []
    for ud in users_data:
        existing = db.query(User).filter(User.username == ud["username"]).first()
        if not existing:
            skills = ud.pop("skills", None)
            user = User(
                username=ud["username"],
                password_hash=hash_password(ud["password"]),
                full_name=ud["full_name"],
                role=ud["role"],
                wind_farm_id=ud.get("wind_farm_id"),
                skills=skills,
                location_lat=39.9 if "operator" in ud["username"] else None,
                location_lng=116.4 if "operator" in ud["username"] else None
            )
            db.add(user)
            created.append(ud["username"])
    db.commit()
    return {"message": "初始化完成", "created_users": created}
