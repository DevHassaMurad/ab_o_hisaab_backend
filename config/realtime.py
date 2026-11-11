"""Live notifications over WebSocket.

One process-wide hub keeps the open sockets per user id. Notifications are also stored in the
`notifications` table, so anything missed while offline appears when the dashboard loads.
Note: this hub lives in one process. With several API workers, put a broker (e.g. Redis pub/sub) behind it.
"""
import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger(__name__)


class NotificationHub:
    def __init__(self):
        self._sockets = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._sockets[user_id].add(websocket)

    async def disconnect(self, user_id, websocket: WebSocket):
        async with self._lock:
            sockets = self._sockets.get(user_id)
            if sockets:
                sockets.discard(websocket)
                if not sockets:
                    self._sockets.pop(user_id, None)

    async def send_to(self, user_id, message: dict):
        """Push a JSON message to every open socket of this user. Dead sockets are dropped."""
        async with self._lock:
            targets = list(self._sockets.get(user_id, ()))
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:  # closed or broken socket
                log.info("Dropping a closed notification socket.")
                await self.disconnect(user_id, ws)


hub = NotificationHub()
