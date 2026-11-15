"""
Envío en tiempo real del estado del panel por WebSocket (`/ws/monitor`).

InfluxDB no avisa de los datos nuevos, así que el servidor consulta cada
MONITOR_WS_INTERVAL segundos (una sola consulta para todos los clientes,
en vez de una por navegador) y solo envía cuando algo ha cambiado.
"""
import asyncio
import json
import logging
import os
from typing import Callable, Optional

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)

MONITOR_WS_INTERVAL = float(os.getenv("MONITOR_WS_INTERVAL", "5"))


class MonitorHub:
    def __init__(self, build_snapshot: Callable[[], dict], interval: float = MONITOR_WS_INTERVAL):
        self._build = build_snapshot
        self._interval = interval
        self._clients: set[WebSocket] = set()
        self._last: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        if self._last is not None:
            await ws.send_text(self._last)  # estado actual nada más conectar
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def _run(self) -> None:
        # El bucle solo vive mientras hay alguien conectado.
        while self._clients:
            try:
                snapshot = await asyncio.to_thread(self._build)
                text = json.dumps(jsonable_encoder(snapshot))
                if text != self._last:
                    self._last = text
                    await self._broadcast(text)
            except Exception:
                logger.exception("Error actualizando el monitor en tiempo real")
            await asyncio.sleep(self._interval)
        self._last = None  # sin clientes, el próximo empieza con datos frescos

    async def _broadcast(self, text: str) -> None:
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.disconnect(ws)
