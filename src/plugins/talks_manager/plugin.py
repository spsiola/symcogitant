import asyncio
from typing import Any, Dict
from src.core.base import BaseAgentPlugin
from .talk import Talk

class TalksManagerPlugin(BaseAgentPlugin):
    """
    Универсальный менеджер диалогов (TalksManager).
    Обеспечивает абстракцию "Комнат" (Rooms/Talks) поверх любых транспортных протоколов.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Универсальный диспетчер диалогов и контекста (TalksManager)"
        
        # Реестр активных разговоров: talk_id -> TalkSession
        self.sessions = {}
        
        # Реестр зарегистрированных транспортов и их возможностей (Capabilities)
        # Пример: {"telegram": {"tools": ["reply", "edit_message", "set_reaction"], "media_support": True}}
        self.transports_capabilities = {}
        
        # Реестр ожидающих запросов к LLM (request_id -> talk_id)
        self.pending_requests = {}

    async def run(self):
        # Предзагружаем все сохраненные комнаты (чтобы запустить их фоновые процессы)
        self._preload_active_talks()
        
        # Подписываемся на универсальные события от транспортов
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log("TalksManager started. Waiting for transport registrations...", "INFO")
        
        self.running = True
        while self.running:
            await asyncio.sleep(1)

    def _preload_active_talks(self):
        import os, json
        talks_dir = os.path.join(os.getcwd(), "data", "talks")
        if not os.path.exists(talks_dir):
            return
            
        count = 0
        for root, dirs, files in os.walk(talks_dir):
            if "profile.json" in files:
                rel_path = os.path.relpath(root, talks_dir)
                parts = rel_path.split(os.sep)
                if len(parts) >= 3:
                    protocol = parts[0]
                    talk_type = parts[1]
                    target_id = parts[2]
                    topic = parts[3] if len(parts) > 3 else None
                    
                    talk_id = f"{protocol}:{talk_type}:{target_id}"
                    if topic and topic.startswith("topic_"):
                        talk_id += f":topic:{topic.split('_')[1]}"
                        
                    if talk_id not in self.sessions:
                        try:
                            with open(os.path.join(root, "profile.json"), "r", encoding="utf-8") as f:
                                data = json.load(f)
                            status = data.get("profile", {}).get("status", "ACTIVE")
                            if status in ["OBSERVER", "FROZEN", "DEAD"]:
                                continue
                        except Exception:
                            pass
                            
                        self.sessions[talk_id] = Talk(talk_id, self, self.config)
                        count += 1
                        
        if count > 0:
            print(f"[TalksManagerPlugin] Preloaded {count} active talks")

    async def _handle_event(self, event: dict):
        event_type = event.get("type")
        
        # Регистрация возможностей транспорта
        if event_type == "transport_register":
            protocol = event.get("protocol")
            capabilities = event.get("capabilities", {})
            if protocol:
                self.transports_capabilities[protocol] = capabilities
                await self.emit_log(f"Registered transport '{protocol}' with capabilities: {capabilities}", "INFO")
                
                # Для всех предзагруженных диалогов запрашиваем историю, если они еще ждут её
                for talk_id, talk in self.sessions.items():
                    if talk_id.startswith(f"{protocol}:") and talk.history_sync_pending:
                        await self.event_bus.publish({
                            "type": "talk_history_sync_request",
                            "protocol": protocol,
                            "talk_id": talk_id,
                            "limit": 100
                        })
                
        # Обработка событий внутри конкретного диалога
        elif event_type in [
            "talk_message_received", 
            "talk_message_edited", 
            "talk_message_deleted", 
            "talk_reaction_changed", 
            "talk_history_sync_response",
            "talk_message_sent",
            "talk_interlocutor_status"
        ]:
            talk_id = event.get("talk_id")
            if not talk_id:
                return
                
            if talk_id not in self.sessions:
                # Не создаем новую сессию для второстепенных событий, чтобы не засорять память
                if event_type in ["talk_interlocutor_status"]:
                    return
                    
                self.sessions[talk_id] = Talk(talk_id, self, self.config)
                await self.emit_log(f"Created new universal Talk session for {talk_id}", "INFO")
                # Request history sync for the new talk session
                await self.event_bus.publish({
                    "type": "talk_history_sync_request",
                    "protocol": talk_id.split(":")[0],
                    "talk_id": talk_id,
                    "limit": 100
                })
                
            talk = self.sessions[talk_id]
            await talk.handle_event(event_type, event)
            
        elif event_type == "talk_message_deleted_global":
            msg_id = event.get("msg_id")
            protocol = event.get("protocol", "tg")
            for t_id, talk in self.sessions.items():
                if t_id.startswith(f"{protocol}:"):
                    await talk.handle_event("talk_message_deleted", {"msg_id": msg_id})
            
        elif event_type in ["llm_response", "llm_response_error"]:
            req_id = event.get("request_id")
            if not req_id or req_id not in self.pending_requests:
                return
                
            talk_id = self.pending_requests.pop(req_id)
            if talk_id in self.sessions:
                talk = self.sessions[talk_id]
                await talk.handle_llm_response(event)
