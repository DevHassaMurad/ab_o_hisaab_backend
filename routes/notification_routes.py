from datetime import datetime

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from jwt.exceptions import InvalidTokenError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from config.database import get_db
from config.jwt_handler import JwtHandler
from config.realtime import hub
from controller.authentication import AuthFlowError
from middlewares.auth_middleware import auth_middleware
from model.notification_model import Notification
from model.user_model import User

router = APIRouter(tags=["Notifications"])


@router.get("/notifications")
def list_notifications(
    limit: int = Query(default=30, ge=1, le=100), user=Depends(auth_middleware), db: Session = Depends(get_db)
):
    items = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ).all()
    unread = db.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return {"items": [n.to_dict() for n in items], "unread": int(unread or 0)}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: int, user=Depends(auth_middleware), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise AuthFlowError("NOTIFICATION_NOT_FOUND", "This notification does not exist.", 404)
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
    return n.to_dict()


@router.post("/notifications/read-all")
def mark_all_read(user=Depends(auth_middleware), db: Session = Depends(get_db)):
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.utcnow())
    )
    db.commit()
    return {"unread": 0}


def _user_from_token(token: str, db: Session):
    """Same checks as auth_middleware, for the WebSocket handshake (browsers cannot send headers there)."""
    try:
        payload = JwtHandler().verify_token(token)
        user_id = int(payload.get("sub"))
    except (InvalidTokenError, TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active or not user.email_verified_at or payload.get("ver", 0) != user.token_version:
        return None
    return user.id


@router.websocket("/ws/notifications")
async def notifications_socket(websocket: WebSocket, token: str = Query(default=""), db: Session = Depends(get_db)):
    """Live notifications. Connect with ?token=<access token>; the server pushes
    {"event": "notification", "notification": {...}}. Send "ping" to keep the connection alive."""
    user_id = _user_from_token(token, db)
    db.close()  # only needed for the handshake; do not hold a connection for the socket's lifetime
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await hub.connect(user_id, websocket)
    try:
        await websocket.send_json({"event": "connected"})
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(user_id, websocket)
