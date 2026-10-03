import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, events, utils
from telethon.tl.types import ReactionEmoji, UpdateMessageReactions
from telethon.tl.functions.messages import SendReactionRequest

from src.core.base import BaseAgentPlugin

class TelegramTransportPlugin(BaseAgentPlugin):
    """
    Транспортный слой Telegram.
    Транслирует события платформы в универсальные события (TalksManager) и наоборот.
    Включает поддержку медиа, форумов (топиков), реакций и модерации.
    """
    def __init__(self, config, event_bus, core=None):
        super().__init__(config, event_bus, core=core)
        self.description = "Telegram Transport Adapter for TalksManager"
        
        env_path = os.path.join("data", ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
            
        self.api_id = os.environ.get("TG_API_ID")
        self.api_hash = os.environ.get("TG_API_HASH")
        
        self.session_path = self.config.get("session_path", "data/telegram_user")
        self.client: TelegramClient | None = None
        self.my_id = None
        self.typing_tasks: dict[str, asyncio.Task] = {}

    def _get_talk_id(self, chat_id, topic_id=None) -> str | None:
        if chat_id is None:
            return None
        if topic_id:
            return f"tg:forum:{chat_id}:topic:{topic_id}"
        elif chat_id > 0:
            return f"tg:priv:{chat_id}"
        else:
            return f"tg:group:{chat_id}"

    async def _get_sender_info(self, sender_id) -> dict:
        name = "Unknown"
        role = "user"
        if sender_id:
            try:
                entity = await self.client.get_entity(sender_id)
                name = utils.get_display_name(entity)
                if getattr(entity, 'bot', False):
                    role = "bot"
            except Exception:
                pass
        return {
            "id": str(sender_id),
            "name": name,
            "role": role,
            "is_me": sender_id == self.my_id
        }

    def _extract_media_placeholder(self, msg) -> str:
        # Аналог из Praxis: если есть медиа, добавляем текстовый маркер
        if not msg.media:
            return ""
            
        media_text = "[Вложение]"
        if msg.photo:
            media_text = "[Фотография]"
        elif msg.video:
            media_text = f"[Видео, {msg.video.attributes[0].duration}с]" if msg.video.attributes else "[Видео]"
        elif msg.voice:
            duration = msg.voice.attributes[0].duration if msg.voice.attributes else 0
            media_text = f"[Голосовое сообщение, {duration}с]"
        elif msg.audio:
            media_text = "[Аудио]"
        elif getattr(msg, 'document', None):
            media_text = "[Документ]"
            
        # Для стикеров часто используется атрибут документа, иногда это особый тип.
        if msg.sticker:
            alt = msg.sticker.attributes[1].alt if len(msg.sticker.attributes) > 1 and hasattr(msg.sticker.attributes[1], 'alt') else ""
            media_text = f"[Стикер {alt}]"
            
        return media_text

    async def run(self):
        self.event_bus.subscribe(self._handle_event)
        
        if not self.api_id or not self.api_hash:
            await self.emit_log("TG_API_ID or TG_API_HASH not set", "ERROR")
            return
            
        try:
            self.client = TelegramClient(self.session_path, int(self.api_id), self.api_hash)
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                await self.emit_log("Telegram session not authorized.", "ERROR")
                return

            me = await self.client.get_me()
            self.my_id = me.id
            self.my_name = utils.get_display_name(me)
            
            # Регистрируем Transport в TalksManager
            await self.event_bus.publish({
                "type": "transport_register",
                "protocol": "tg",
                "identity": {
                    "agent_id": str(self.my_id),
                    "agent_name": self.my_name
                },
                "capabilities": {
                    "can_send_text": True,
                    "can_send_media": True,
                    "can_edit": True,
                    "can_delete": True,
                    "can_react": True,
                    "can_download_media": True
                }
            })
            
            await self.emit_log("Connected to Telegram and registered transport.", "INFO")

            @self.client.on(events.NewMessage)
            async def new_message_handler(event):
                msg = event.message
                await self.emit_log(f"[INCOMING TG] NewMessage: chat_id={event.chat_id}, sender_id={msg.sender_id}, text='{msg.message}'", "DEBUG")
                talk_id = self._get_talk_id(event.chat_id, getattr(msg, 'reply_to_msg_id', None) if getattr(msg, 'is_reply', False) and getattr(event.chat, 'forum', False) else None)
                
                sender = await self._get_sender_info(msg.sender_id)
                text = msg.message or ""
                media_placeholder = self._extract_media_placeholder(msg)
                
                chat = await event.get_chat()
                chat_title = utils.get_display_name(chat) if chat else "Unknown"
                
                if media_placeholder:
                    text = f"{media_placeholder}\n{text}".strip()
                    
                univ_msg = {
                    "talk_id": talk_id,
                    "msg_id": str(msg.id),
                    "protocol": "tg",
                    "date": msg.date.isoformat(),
                    "sender": sender,
                    "text": text,
                    "is_outgoing": getattr(msg, 'out', False),
                    "is_deleted": False,
                    "is_edited": False,
                    "mentions_me": getattr(msg, 'mentioned', False),
                    "reply_to_msg_id": str(msg.reply_to_msg_id) if getattr(msg, 'reply_to_msg_id', None) else None
                }
                
                out_payload = {
                    "type": "talk_message_received",
                    "talk_id": talk_id,
                    "message": univ_msg,
                    "chat_info": {
                        "title": chat_title,
                        "is_forum": getattr(event.chat, 'forum', False)
                    }
                }
                await self.emit_log(f"[OUTGOING EVENT_BUS] talk_message_received: {out_payload}", "DEBUG")
                await self.event_bus.publish(out_payload)

            @self.client.on(events.UserUpdate)
            async def user_update_handler(event):
                if not event.user_id:
                    return
                # We can't always know chat_id for private chats perfectly if it's just a user update,
                # but for private dialogs user_id == chat_id.
                talk_id = self._get_talk_id(event.user_id)
                
                status_type = None
                if event.typing:
                    status_type = "typing"
                elif event.online:
                    status_type = "online"
                elif event.recently or event.offline or event.within_months or event.within_weeks:
                    status_type = "offline"
                    
                if status_type and talk_id:
                    await self.event_bus.publish({
                        "type": "talk_interlocutor_status",
                        "talk_id": talk_id,
                        "status": status_type,
                        "user_id": str(event.user_id)
                    })

            @self.client.on(events.MessageDeleted)
            async def delete_handler(event):
                await self.emit_log(f"[INCOMING TG] MessageDeleted: chat_id={event.chat_id}, deleted_ids={event.deleted_ids}", "DEBUG")
                talk_id = self._get_talk_id(event.chat_id)
                for msg_id in event.deleted_ids:
                    if talk_id:
                        await self.event_bus.publish({
                            "type": "talk_message_deleted",
                            "talk_id": talk_id,
                            "msg_id": str(msg_id)
                        })
                    else:
                        await self.event_bus.publish({
                            "type": "talk_message_deleted_global",
                            "msg_id": str(msg_id),
                            "protocol": "tg"
                        })

            @self.client.on(events.MessageEdited)
            async def edit_handler(event):
                msg = event.message
                await self.emit_log(f"[INCOMING TG] MessageEdited: msg_id={msg.id}, text='{msg.message}', reactions={getattr(msg, 'reactions', None)}", "DEBUG")
                talk_id = self._get_talk_id(event.chat_id, getattr(msg, 'reply_to_msg_id', None) if getattr(msg, 'is_reply', False) and getattr(event.chat, 'forum', False) else None)
                
                # Обработка изменения текста
                text = msg.message or ""
                media_placeholder = self._extract_media_placeholder(msg)
                if media_placeholder:
                    text = f"{media_placeholder}\n{text}".strip()
                    
                out_payload = {
                    "type": "talk_message_edited",
                    "talk_id": talk_id,
                    "msg_id": str(msg.id),
                    "text": text,
                    "edited_date": msg.edit_date.isoformat() if getattr(msg, 'edit_date', None) else None
                }
                await self.emit_log(f"[OUTGOING EVENT_BUS] talk_message_edited: {out_payload}", "DEBUG")
                await self.event_bus.publish(out_payload)
                
                # Обработка реакций (в Telethon изменения реакций часто приходят как MessageEdited)
                if hasattr(msg, 'reactions'):
                    reactions = []
                    if msg.reactions and getattr(msg.reactions, 'recent_reactions', None):
                        for r in msg.reactions.recent_reactions:
                            p_id = utils.get_peer_id(r.peer_id)
                            emo = r.reaction.emoticon if hasattr(r.reaction, 'emoticon') else "[CustomEmoji]"
                            reactions.append({"user_id": str(p_id), "emoji": emo})
                        
                    out_payload = {
                        "type": "talk_reaction_changed",
                        "talk_id": talk_id,
                        "msg_id": str(msg.id),
                        "reactions": reactions
                    }
                    await self.emit_log(f"[OUTGOING EVENT_BUS] talk_reaction_changed (from edit): {out_payload}", "DEBUG")
                    await self.event_bus.publish(out_payload)

            @self.client.on(events.Raw)
            async def raw_handler(event):
                await self.emit_log(f"[INCOMING TG RAW] type={type(event).__name__} | payload={event}", "DEBUG")

                if isinstance(event, UpdateMessageReactions):
                    await self.emit_log(f"Raw reaction event: {event}", "DEBUG")
                    chat_id = utils.get_peer_id(event.peer)
                    talk_id = self._get_talk_id(chat_id)
                    
                    reactions = []
                    if getattr(event, 'reactions', None) and getattr(event.reactions, 'recent_reactions', None):
                        for r in event.reactions.recent_reactions:
                            p_id = utils.get_peer_id(r.peer_id)
                            emo = r.reaction.emoticon if hasattr(r.reaction, 'emoticon') else "[CustomEmoji]"
                            reactions.append({"user_id": str(p_id), "emoji": emo})
                    elif getattr(event, 'reactions', None) is None or not getattr(event.reactions, 'recent_reactions', None):
                        await self.emit_log("Reactions is None or empty in event", "DEBUG")
                            
                    out_payload = {
                        "type": "talk_reaction_changed",
                        "talk_id": talk_id,
                        "msg_id": str(event.msg_id),
                        "reactions": reactions
                    }
                    await self.emit_log(f"[OUTGOING EVENT_BUS] talk_reaction_changed (from raw): {out_payload}", "DEBUG")
                    await self.event_bus.publish(out_payload)

            await self.client.run_until_disconnected()
        except Exception as e:
            await self.emit_log(f"Telegram client error: {e}", "ERROR")

    async def stop(self):
        self.running = False
        for task in self.typing_tasks.values():
            task.cancel()
        self.typing_tasks.clear()
        if self.client:
            try:
                from telethon.tl.functions.account import UpdateStatusRequest
                await self.client(UpdateStatusRequest(offline=True))
                await self.emit_log("Set offline status", "INFO")
            except Exception as e:
                await self.emit_log(f"Failed to set offline status: {e}", "WARNING")
            await self.client.disconnect()
        await super().stop()

    async def _handle_event(self, event: dict):
        event_type = event.get("type")
        protocol = event.get("protocol")
        talk_id = event.get("talk_id")
        
        if protocol != "tg" or not self.client or not talk_id:
            return
            
        await self.emit_log(f"[INCOMING EVENT_BUS] {event_type} | event_data={event}", "DEBUG")
            
        if event_type == "talk_send_message":
            text = event.get("text")
            
            if not text or "[NO ANSWER]" in text:
                await self.emit_log(f"Message ignored due to [NO ANSWER] or empty text: {text}", "INFO")
                # Отправляем фейковое событие, чтобы сбросить статус "печатает" (action: cancel),
                # но так как в новой архитектуре это делается по-другому, просто выходим.
                return
            
            # Извлекаем chat_id из talk_id
            parts = talk_id.split(":")
            chat_id = int(parts[2])
            topic_id = int(parts[4]) if len(parts) > 4 and parts[3] == "topic" else None
            
            try:
                # Отправка сообщения
                sent_msg = await self.client.send_message(chat_id, text, reply_to=topic_id)
                
                # Публикация подтверждения (опционально)
                await self.event_bus.publish({
                    "type": "talk_message_sent",
                    "talk_id": talk_id,
                    "message": {
                        "talk_id": talk_id,
                        "msg_id": str(sent_msg.id),
                        "protocol": "tg",
                        "date": sent_msg.date.isoformat(),
                        "sender": {"id": str(self.my_id), "name": self.my_name, "role": "assistant", "is_me": True},
                        "text": text,
                        "is_outgoing": True
                    }
                })
            except Exception as e:
                await self.emit_log(f"Send failed: {e}", "ERROR")

        elif event_type == "talk_mark_read":
            parts = talk_id.split(":")
            chat_id = int(parts[2])
            try:
                await self.client.send_read_acknowledge(chat_id)
                await self.emit_log(f"Marked {talk_id} as read", "DEBUG")
            except Exception as e:
                await self.emit_log(f"Failed to mark read {talk_id}: {e}", "ERROR")

        elif event_type == "talk_set_action":
            action = event.get("action", "cancel")
            parts = talk_id.split(":")
            chat_id = int(parts[2])
            
            # Отменяем предыдущую задачу, если есть
            if talk_id in self.typing_tasks:
                self.typing_tasks[talk_id].cancel()
                del self.typing_tasks[talk_id]
                
            if action != "cancel":
                async def _typing_worker(peer, act):
                    try:
                        async with self.client.action(peer, act):
                            # Висим в контекстном менеджере, пока нас не отменят
                            # Telethon сам поддерживает отправку статуса каждые 5-10 секунд
                            await asyncio.sleep(300) 
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        await self.emit_log(f"Typing action error: {e}", "ERROR")
                
                self.typing_tasks[talk_id] = asyncio.create_task(_typing_worker(chat_id, action))
                await self.emit_log(f"Started typing action '{action}' for {talk_id}", "DEBUG")
                
        elif event_type == "talk_history_sync_request":
            limit = event.get("limit", 100)
            await self.emit_log(f"Received sync request for {talk_id}", "DEBUG")
            
            parts = talk_id.split(":")
            chat_id = int(parts[2])
            topic_id = int(parts[4]) if len(parts) > 4 and parts[3] == "topic" else None
            
            asyncio.create_task(self._sync_history(talk_id, chat_id, topic_id, limit))
            
        elif event_type == "talk_set_reaction":
            msg_id = int(event.get("msg_id", 0))
            emoji = event.get("emoji")
            
            parts = talk_id.split(":")
            chat_id = int(parts[2])
            try:
                await self.client(SendReactionRequest(
                    peer=chat_id,
                    msg_id=msg_id,
                    reaction=[ReactionEmoji(emoticon=emoji)] if emoji else []
                ))
            except Exception as e:
                await self.emit_log(f"Failed to set reaction: {e}", "ERROR")

        elif event_type == "talk_delete_message":
            msg_id = int(event.get("msg_id", 0))
            parts = talk_id.split(":")
            chat_id = int(parts[2])
            try:
                await self.client.delete_messages(chat_id, [msg_id])
            except Exception as e:
                await self.emit_log(f"Failed to delete msg: {e}", "ERROR")

        elif event_type == "talk_ban_user":
            user_id = int(event.get("user_id", 0))
            parts = talk_id.split(":")
            chat_id = int(parts[2])
            try:
                # Требуются права администратора в группе
                await self.client.edit_permissions(chat_id, user_id, view_messages=False)
                await self.emit_log(f"Banned user {user_id} in {chat_id}", "INFO")
            except Exception as e:
                await self.emit_log(f"Failed to ban user: {e}", "ERROR")

    async def _sync_history(self, talk_id: str, chat_id: int, topic_id: int | None, limit: int):
        try:
            messages = []
            reply_to = topic_id if topic_id else None
            async for msg in self.client.iter_messages(chat_id, limit=limit, reply_to=reply_to):
                if getattr(msg, 'action', None):
                    continue  # Пропускаем сервисные сообщения
                    
                sender = await self._get_sender_info(msg.sender_id)
                text = msg.message or ""
                media_placeholder = self._extract_media_placeholder(msg)
                if media_placeholder:
                    text = f"{media_placeholder}\n{text}".strip()
                    
                reactions = []
                if hasattr(msg, 'reactions') and getattr(msg.reactions, 'recent_reactions', None):
                    for r in msg.reactions.recent_reactions:
                        p_id = utils.get_peer_id(r.peer_id)
                        emo = r.reaction.emoticon if hasattr(r.reaction, 'emoticon') else "[CustomEmoji]"
                        reactions.append({"user_id": str(p_id), "emoji": emo})
                        
                messages.append({
                    "talk_id": talk_id,
                    "msg_id": str(msg.id),
                    "protocol": "tg",
                    "date": msg.date.isoformat() if msg.date else None,
                    "sender": sender,
                    "text": text,
                    "is_outgoing": getattr(msg, 'out', False),
                    "is_deleted": False,
                    "is_edited": getattr(msg, 'edit_date', None) is not None,
                    "edited_date": msg.edit_date.isoformat() if getattr(msg, 'edit_date', None) else None,
                    "reactions": reactions,
                    "mentions_me": getattr(msg, 'mentioned', False),
                    "reply_to_msg_id": str(msg.reply_to_msg_id) if getattr(msg, 'reply_to_msg_id', None) else None
                })
                
            messages.reverse()
            
            await self.emit_log(f"Sync history done for {talk_id}, got {len(messages)} msgs", "DEBUG")
            await self.event_bus.publish({
                "type": "talk_history_sync_response",
                "talk_id": talk_id,
                "messages": messages
            })
        except Exception as e:
            await self.emit_log(f"Sync history failed for {talk_id}: {e}", "ERROR")
