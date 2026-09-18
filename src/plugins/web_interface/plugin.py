import asyncio
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.core.plugin import BasePlugin


class WebInterfacePlugin(BasePlugin):
    def __init__(self, config, event_bus):
        super().__init__(config, event_bus)
        self.app = FastAPI(title="Symcogitant Web Interface")
        self.host = self.config.get("host", "127.0.0.1")
        self.port = self.config.get("port", 4217)
        self.connected_websockets = []
        
        # Настройка статики
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        self.app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @self.app.get("/")
        async def read_index():
            return FileResponse(os.path.join(static_dir, "index.html"))

        @self.app.websocket("/ws/logs")
        async def websocket_logs(websocket: WebSocket):
            await websocket.accept()
            self.connected_websockets.append(websocket)
            try:
                while True:
                    await websocket.receive_text()
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass
            finally:
                if websocket in self.connected_websockets:
                    self.connected_websockets.remove(websocket)

        # Подписка на шину событий
        self.event_bus.subscribe(self._handle_event)

    async def _handle_event(self, event: dict):
        if event.get("type") == "log":
            for ws in list(self.connected_websockets):
                try:
                    await ws.send_json(event)
                except RuntimeError:
                    self.connected_websockets.remove(ws)

    async def run(self):
        uvicorn_config = uvicorn.Config(
            app=self.app, 
            host=self.host, 
            port=self.port, 
            log_level="warning", 
            loop="asyncio"
        )
        self.server = uvicorn.Server(uvicorn_config)
        await self.emit_log(f"Starting web interface on http://{self.host}:{self.port}")
        await self.server.serve()

    async def stop(self):
        self.running = False
        if hasattr(self, "server") and not self.server.should_exit:
            self.server.should_exit = True
        
        # Плавно ждем завершения uvicorn, не вызывая task.cancel(), 
        # чтобы избежать трейсбеков от внутренних корутин starlette.
        if self.task:
            try:
                await self.task
            except asyncio.CancelledError:
                pass
