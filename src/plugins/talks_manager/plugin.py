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
        # Подписываемся на универсальные события от транспортов
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log("TalksManager started. Waiting for transport registrations...", "INFO")
        
        self.running = True
        while self.running:
            await asyncio.sleep(1)

    async def _handle_event(self, event: dict):
        event_type = event.get("type")
        
        # Регистрация возможностей транспорта
        if event_type == "transport_register":
            protocol = event.get("protocol")
            capabilities = event.get("capabilities", {})
            if protocol:
                self.transports_capabilities[protocol] = capabilities
                await self.emit_log(f"Registered transport '{protocol}' with capabilities: {capabilities}", "INFO")
                
        # Обработка событий внутри конкретного диалога
        elif event_type in [
            "talk_message_received", 
            "talk_message_edited", 
            "talk_message_deleted", 
            "talk_reaction_changed", 
            "talk_history_sync_response",
            "talk_message_sent"
        ]:
            talk_id = event.get("talk_id")
            if not talk_id:
                return
                
            if talk_id not in self.sessions:
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
