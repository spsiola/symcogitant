import asyncio
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
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

        @self.app.get("/api/config")
        async def get_config():
            import yaml
            try:
                with open("config.yaml", "r", encoding="utf-8") as f:
                    defaults = yaml.safe_load(f) or {}
            except FileNotFoundError:
                defaults = {}
            try:
                with open("data/config.yaml", "r", encoding="utf-8") as f:
                    user = yaml.safe_load(f) or {}
            except FileNotFoundError:
                user = {}
            return {"defaults": defaults, "user": user}

        @self.app.post("/api/config")
        async def save_config(request: Request):
            import yaml
            data = await request.json()
            os.makedirs("data", exist_ok=True)
            with open("data/config.yaml", "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            return {"status": "ok"}

        @self.app.get("/api/talks")
        async def get_talks():
            import json
            talks = []
            base_dir = "data/talks"
            if not os.path.exists(base_dir):
                return talks
            for protocol in os.listdir(base_dir):
                protocol_path = os.path.join(base_dir, protocol)
                if not os.path.isdir(protocol_path): continue
                for talk_type in os.listdir(protocol_path):
                    type_path = os.path.join(protocol_path, talk_type)
                    if not os.path.isdir(type_path): continue
                    for room_id in os.listdir(type_path):
                        room_path = os.path.join(type_path, room_id)
                        profile_path = os.path.join(room_path, "profile.json")
                        if os.path.isfile(profile_path):
                            try:
                                with open(profile_path, "r", encoding="utf-8") as f:
                                    profile_data = json.load(f)
                                title = profile_data.get("profile", {}).get("title", f"{protocol} {room_id}")
                                talks.append({
                                    "id": f"{protocol}:{talk_type}:{room_id}",
                                    "protocol": protocol,
                                    "talk_type": talk_type,
                                    "room_id": room_id,
                                    "name": title
                                })
                            except Exception as e:
                                pass
            return talks

        @self.app.get("/api/talk_profile")
        async def get_talk_profile(talk_id: str):
            import json
            parts = talk_id.split(":")
            if len(parts) < 3: raise HTTPException(status_code=400, detail="Invalid talk_id")
            protocol, talk_type = parts[0], parts[1]
            room_id = ":".join(parts[2:])
            profile_path = f"data/talks/{protocol}/{talk_type}/{room_id}/profile.json"
            if not os.path.exists(profile_path):
                raise HTTPException(status_code=404, detail="Profile not found")
            with open(profile_path, "r", encoding="utf-8") as f:
                return json.load(f)

        @self.app.post("/api/talk_profile")
        async def save_talk_profile(talk_id: str, request: Request):
            import json
            parts = talk_id.split(":")
            if len(parts) < 3: raise HTTPException(status_code=400, detail="Invalid talk_id")
            protocol, talk_type = parts[0], parts[1]
            room_id = ":".join(parts[2:])
            data = await request.json()
            profile_path = f"data/talks/{protocol}/{talk_type}/{room_id}/profile.json"
            os.makedirs(os.path.dirname(profile_path), exist_ok=True)
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # Publish event so TalksManager can hot-reload
            asyncio.create_task(self.event_bus.publish({
                "type": "talk_profile_updated",
                "talk_id": talk_id,
                "protocol": protocol,
                "room_id": room_id,
                "source": "WebInterfacePlugin"
            }))
            return {"status": "ok"}


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
            
            if self.core:
                harness_status = {
                    "type": "harness_status",
                    "source": "harness core",
                    "daemons": [m.get_status() for m in getattr(self.core, 'daemons', [])],
                    "workers": [m.get_status() for m in getattr(self.core, 'workers', [])],
                    "interceptors": [m.get_status() for m in getattr(self.core, 'interceptors', [])],
                    "plugins": [m.get_status() for m in getattr(self.core, 'plugins', [])],
                }
                await websocket.send_json(harness_status)
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
