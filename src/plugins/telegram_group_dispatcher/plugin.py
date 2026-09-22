import asyncio
import re
from typing import Any, Dict
from src.core.base import BaseAgentPlugin
from .session import GroupDialogSession

class TelegramGroupDispatcher(BaseAgentPlugin):
    """
    Маршрутизатор для групповых сообщений Telegram.
    Управляет сессиями (GroupDialogSession) по chat_id.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Диспетчер для маршрутизации и обработки сообщений в групповых чатах."
        self.sessions = {} # chat_id -> GroupDialogSession
        self.pending_llm_requests = {} # request_id -> chat_id
        
    async def run(self):
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log("TelegramGroupDispatcher started.", "INFO")
        self.running = True
        while self.running:
            await asyncio.sleep(1)

    async def _handle_event(self, event: dict):
        event_type = event.get("type")
        
        if event_type == "telegram_new_message":
            if not event.get("is_private"):
                chat_id = event.get("chat_id")
                if not chat_id:
                    return
                
                # Get or create session
                if chat_id not in self.sessions:
                    session = GroupDialogSession(chat_id, self, self.config)
                    self.sessions[chat_id] = session
                    asyncio.create_task(session.run())
                    await self.emit_log(f"Created new group session for chat {chat_id}", "INFO")
                    
                session = self.sessions[chat_id]
                await session.incoming_queue.put(event)
                
        elif event_type in ["llm_response", "llm_response_error"]:
            req_id = event.get("request_id")
            if req_id in self.pending_llm_requests:
                chat_id = self.pending_llm_requests.pop(req_id)
                session = self.sessions.get(chat_id)
                if session:
                    await session.llm_resp_queue.put(event)

    async def send_llm_request(self, req_id: str, chat_id: int, request_data: dict):
        self.pending_llm_requests[req_id] = chat_id
        await self.event_bus.publish(request_data)

    async def send_telegram_message(self, chat_id: int, text: str):
        await self.event_bus.publish({
            "type": "telegram_send_message",
            "source": self.__class__.__name__,
            "chat_id": chat_id,
            "text": text
        })
        
    async def close_session(self, chat_id: int):
        if chat_id in self.sessions:
            del self.sessions[chat_id]
            await self.emit_log(f"Closed group session for chat {chat_id} due to timeout or error", "INFO")
