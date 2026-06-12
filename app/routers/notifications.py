from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth import get_current_user, require_roles
from app.services.websocket_push import ws_manager, PushNotificationService
from app.models.models import User, UserRole, Notification
import json
import asyncio

router = APIRouter(prefix="/api/notifications", tags=["通知与消息推送"])


@router.get("/my")
async def get_my_notifications(
    is_read: Optional[bool] = None,
    notification_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    if is_read is not None:
        query = query.filter(Notification.is_read == is_read)
    if notification_type:
        query = query.filter(Notification.notification_type == notification_type)

    total = query.count()
    notifications = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "unread_count": query.filter(Notification.is_read == False).count(),
        "items": notifications
    }


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")
    notification.is_read = True
    notification.read_at = datetime.now()
    db.commit()
    return {"message": "已标记为已读"}


@router.post("/read-all")
async def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).update({
        Notification.is_read: True,
        Notification.read_at: datetime.now()
    }, synchronize_session=False)
    db.commit()
    return {"message": "已全部标记为已读"}


@router.get("/unread-count")
async def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    count = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).count()
    return {"unread_count": count}


@router.get("/ws/status")
async def get_websocket_status(
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))
):
    return {
        "online_users": ws_manager.get_online_users(),
        "online_count": ws_manager.get_online_count(),
        "connections": {
            uid: len(conns) for uid, conns in ws_manager.active_connections.items()
        }
    }


@router.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: int,
    token: Optional[str] = Query(None)
):
    from app.database import SessionLocal
    from app.config import settings
    from jose import JWTError, jwt

    if not token:
        await websocket.close(code=1008, reason="缺少token")
        return

    db = SessionLocal()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            await websocket.close(code=1008, reason="无效token")
            db.close()
            return

        user = db.query(User).filter(User.username == username).first()
        if not user or user.id != user_id:
            await websocket.close(code=1008, reason="用户验证失败")
            db.close()
            return

        await ws_manager.connect(user_id, websocket)

        try:
            unread_count = db.query(Notification).filter(
                Notification.user_id == user_id,
                Notification.is_read == False
            ).count()
            await ws_manager.send_personal_message(
                user_id,
                {
                    "type": "system",
                    "title": "连接成功",
                    "content": f"实时消息推送已连接，您有 {unread_count} 条未读消息",
                    "timestamp": datetime.now().isoformat(),
                    "unread_count": unread_count
                }
            )

            while True:
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                    if data:
                        try:
                            msg = json.loads(data)
                            if msg.get("type") == "ping":
                                await ws_manager.send_personal_message(
                                    user_id,
                                    {"type": "pong", "timestamp": datetime.now().isoformat()}
                                )
                        except json.JSONDecodeError:
                            pass
                except asyncio.TimeoutError:
                    await ws_manager.send_personal_message(
                        user_id,
                        {"type": "ping", "timestamp": datetime.now().isoformat()}
                    )
                    continue

        except WebSocketDisconnect:
            pass
        finally:
            ws_manager.disconnect(user_id, websocket)
    except JWTError:
        await websocket.close(code=1008, reason="Token验证失败")
    finally:
        db.close()


@router.post("/test-push/{user_id}")
async def test_push_message(
    user_id: int,
    title: str,
    content: str,
    msg_type: str = "test",
    current_user: User = Depends(require_roles(UserRole.ADMIN))
):
    await PushNotificationService._build_message(msg_type, title, content)
    await ws_manager.send_personal_message(
        user_id,
        {
            "type": msg_type,
            "title": title,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
    )
    online = ws_manager.is_online(user_id)
    return {
        "message": f"消息已{'推送' if online else '暂存（用户离线）'}给用户 {user_id}",
        "user_online": online
    }
