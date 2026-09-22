import asyncio
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.core.base import BasePlugin


class WebInterfacePlugin(BasePlugin):
    def __init__(self, config, event_bus, core=None):
        super().__init__(config, event_bus, core=core)
        self.description = "Веб-интерфейс платформы и мониторинг в реальном времени."
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
            asyncio.create_task(self.event_bus.publish({
                "type": "ui_client_connected",
                "source": self.__class__.__name__
            }))
            
            # Direct query to SystemMonitorReflex for instant model list
            init_data_payload = {
                "server_start_time": self.start_time * 1000 if self.start_time else None,
                "server_version": getattr(self.core, "version", "unknown") if self.core else "unknown"
            }
            if self.core:
                sys_monitor = self.core.get_daemon("SystemMonitorDaemon")
                if sys_monitor:
                    try:
                        models = sys_monitor.get_models()
                        if models:
                            init_data_payload["llm_models"] = models
                    except Exception as e:
                        await self.emit_log(f"Failed to fetch models from SystemMonitorReflex: {e}", level="ERROR")
                        
            await websocket.send_json({
                "type": "init_data",
                "data": init_data_payload
            })
            try:
                while True:
                    text_data = await websocket.receive_text()
                    try:
                        import json
                        data = json.loads(text_data)
                        if isinstance(data, dict) and "type" in data:
                            # Forward UI events to internal event bus
                            asyncio.create_task(self.event_bus.publish(data))
                    except Exception as e:
                        await self.emit_log(f"Error parsing WS message: {e}", level="ERROR")
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass
            finally:
                if websocket in self.connected_websockets:
                    self.connected_websockets.remove(websocket)

        # Подписка на шину событий
        self.event_bus.subscribe(self._handle_event)

    async def _handle_event(self, event: dict):
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
        url = f"http://{self.host}:{self.port}"
        print(f"\n\033[92m\033[1m🚀 Web Interface is running! Click to open: {url}\033[0m\n")
        await self.emit_log(f"Starting web interface on {url}")
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
