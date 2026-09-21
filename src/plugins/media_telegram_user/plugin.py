import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, events, utils
from telethon.tl.types import UpdateMessageReactions, ReactionEmoji, ReactionCustomEmoji

from src.core.plugin import BasePlugin

class MediaTelegramUserPlugin(BasePlugin):
    """
    Плагин для получения обновлений из Telegram от имени пользователя.
    Слушает входящие сообщения, редактирование и реакции, и транслирует их в EventBus.
    """
    def __init__(self, config, event_bus, core=None):
        super().__init__(config, event_bus, core=core)
        
        env_path = os.path.join("data", ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
            
        self.api_id = os.environ.get("TG_API_ID")
        self.api_hash = os.environ.get("TG_API_HASH")
        
        self.session_path = self.config.get("session_path", "data/telegram_user")
        self.client = None
        
        # Кэш для отслеживания изменений сообщений и реакций
        self.msg_cache = {} # msg_id -> dict {"text": text, "chat_name": name, "sender_name": name}
        self.reactions_cache = {} # msg_id -> set of (peer_id, emoticon)
        
        self._typing_tasks = {} # chat_id -> asyncio.Task

    async def get_entity_name(self, peer_id):
        """Возвращает форматированную строку 'Имя (ID)' для чатов и пользователей."""
        if not peer_id:
            return "Unknown"
        try:
            entity = await self.client.get_entity(peer_id)
            name = utils.get_display_name(entity)
            return f"{name} ({peer_id})"
        except Exception:
            return str(peer_id)

    async def _typing_loop(self, chat_id):
        try:
            async with self.client.action(chat_id, 'typing'):
                await asyncio.Event().wait() # Блокируемся, пока не отменят таску
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self.emit_log(f"Typing action error for {chat_id}: {e}", "ERROR")

    async def run(self):
        self.event_bus.subscribe(self._handle_event)
        
        if not self.api_id or not self.api_hash:
            await self.emit_log("TG_API_ID or TG_API_HASH not set in data/.env", "ERROR")
            return
            
        try:
            self.client = TelegramClient(self.session_path, int(self.api_id), self.api_hash)
            
            await self.client.connect()
            if not await self.client.is_user_authorized():
                await self.emit_log("Telegram session is not authorized. Please run tools/tg_login.py first.", "ERROR")
                return

            await self.emit_log("Connected to Telegram successfully.", "INFO")
            
            # 1. Новые сообщения
            @self.client.on(events.NewMessage)
            async def handler(event):
                msg_id = event.message.id
                is_private = event.is_private
                chat_id_raw = event.chat_id
                sender_name = await self.get_entity_name(event.sender_id)
                chat_name = await self.get_entity_name(event.chat_id)
                text = event.message.message
                
                self.msg_cache[msg_id] = {
                    "text": text,
                    "chat_name": chat_name,
                    "sender_name": sender_name
                }
                
                # Сохраняем начальные реакции (если есть)
                if hasattr(event.message, 'reactions') and getattr(event.message.reactions, 'recent_reactions', None):
                    new_reactions = set()
                    for r in event.message.reactions.recent_reactions:
                        p_id = utils.get_peer_id(r.peer_id)
                        emo = r.reaction.emoticon if hasattr(r.reaction, 'emoticon') else "[CustomEmoji]"
                        new_reactions.add((p_id, emo))
                    self.reactions_cache[msg_id] = new_reactions
                
                sender_name = await self.get_entity_name(event.sender_id)
                chat_name = await self.get_entity_name(event.chat_id)
                text = event.message.message
                
                await self.event_bus.publish({
                    "type": "telegram_new_message",
                    "source": "MediaTelegramUserPlugin",
                    "msg_id": msg_id,
                    "sender": sender_name,
                    "chat": chat_name,
                    "chat_id": chat_id_raw,
                    "is_private": is_private,
                    "text": text
                })
                
                display_text = text if text and len(text) < 100 else (text[:97] + "..." if text else "[Media/Non-text]")
                await self.emit_log(f"Msg in {chat_name} from {sender_name}: {display_text}", "INFO")

            # 2. Отредактированные сообщения и реакции (приходят как Edit)
            @self.client.on(events.MessageEdited)
            async def edit_handler(event):
                msg_id = event.message.id
                sender_name = await self.get_entity_name(event.sender_id)
                chat_name = await self.get_entity_name(event.chat_id)
                new_text = event.message.message
                
                old_state = self.msg_cache.get(msg_id, {})
                old_text = old_state.get("text")
                
                self.msg_cache[msg_id] = {
                    "text": new_text,
                    "chat_name": chat_name,
                    "sender_name": sender_name
                }
                
                # Если текст действительно изменился (или кэш пуст)
                if old_text is None or old_text != new_text:
                    await self.event_bus.publish({
                        "type": "telegram_message_edited",
                        "source": "MediaTelegramUserPlugin",
                        "msg_id": msg_id,
                        "sender": sender_name,
                        "chat": chat_name,
                        "text": new_text
                    })
                    display_text = new_text if new_text and len(new_text) < 100 else (new_text[:97] + "..." if new_text else "[Media/Non-text]")
                    await self.emit_log(f"[EDIT] Msg in {chat_name} from {sender_name}: {display_text}", "INFO")

                # Проверяем изменения реакций
                if hasattr(event.message, 'reactions') and getattr(event.message.reactions, 'recent_reactions', None) is not None:
                    new_reactions = set()
                    for r in event.message.reactions.recent_reactions:
                        p_id = utils.get_peer_id(r.peer_id)
                        emo = r.reaction.emoticon if hasattr(r.reaction, 'emoticon') else "[CustomEmoji]"
                        new_reactions.add((p_id, emo))
                        
                    old_reactions = self.reactions_cache.get(msg_id, set())
                    self.reactions_cache[msg_id] = new_reactions
                    
                    added = new_reactions - old_reactions
                    removed = old_reactions - new_reactions
                    
                    for p_id, emo in added:
                        reactor_name = await self.get_entity_name(p_id)
                        await self.emit_log(f"[REACTION ADDED] {emo} от {reactor_name} на сообщение в {chat_name}", "INFO")
                        
                    for p_id, emo in removed:
                        reactor_name = await self.get_entity_name(p_id)
                        await self.emit_log(f"[REACTION REMOVED] {emo} от {reactor_name} на сообщение в {chat_name}", "INFO")
                elif hasattr(event.message, 'reactions') and not getattr(event.message.reactions, 'recent_reactions', None):
                    # Все недавние реакции были удалены
                    old_reactions = self.reactions_cache.get(msg_id, set())
                    self.reactions_cache[msg_id] = set()
                    for p_id, emo in old_reactions:
                        reactor_name = await self.get_entity_name(p_id)
                        await self.emit_log(f"[REACTION REMOVED] {emo} от {reactor_name} на сообщение в {chat_name}", "INFO")

                # Простая очистка кэша, чтобы не текло в бесконечность
                if len(self.msg_cache) > 1000:
                    self.msg_cache.clear()
                    self.reactions_cache.clear()

            # 3. Удаленные сообщения
            @self.client.on(events.MessageDeleted)
            async def delete_handler(event):
                for msg_id in event.deleted_ids:
                    cached_msg = self.msg_cache.get(msg_id, {})
                    old_text = cached_msg.get("text")
                    
                    # Пытаемся достать chat_name из кэша, если в event.chat_id пусто
                    chat_name = cached_msg.get("chat_name")
                    if not chat_name:
                        chat_name = await self.get_entity_name(event.chat_id) if event.chat_id else "Unknown Chat"
                    
                    display_text = old_text if old_text and len(old_text) < 100 else (old_text[:97] + "..." if old_text else f"[ID:{msg_id} / Unknown Text]")
                    
                    await self.event_bus.publish({
                        "type": "telegram_message_deleted",
                        "source": "MediaTelegramUserPlugin",
                        "chat": chat_name,
                        "msg_id": msg_id,
                        "text": old_text
                    })
                    
                    await self.emit_log(f"[DELETED] Msg in {chat_name}: {display_text}", "WARNING")
                    
                    # Очищаем кэш для удаленного сообщения
                    self.msg_cache.pop(msg_id, None)
                    self.reactions_cache.pop(msg_id, None)

            await self.client.run_until_disconnected()
            
        except Exception as e:
            await self.emit_log(f"Telegram client error: {e}", "ERROR")

    async def stop(self):
        self.running = False
        if self.client:
            await self.client.disconnect()
            
        await super().stop()

    async def _handle_event(self, event: dict):
        event_type = event.get("type")
        
        if event_type == "telegram_send_message":
            chat_id = event.get("chat_id")
            text = event.get("text")
            
            task = self._typing_tasks.pop(chat_id, None)
            if task:
                task.cancel()
                
            if chat_id and text and self.client:
                try:
                    await self.client.send_message(chat_id, text)
                    await self.emit_log(f"Sent message to {chat_id}: {text[:50]}...", "INFO")
                except Exception as e:
                    await self.emit_log(f"Failed to send message to {chat_id}: {e}", "ERROR")
                    
        elif event_type == "telegram_chat_action":
            chat_id = event.get("chat_id")
            action = event.get("action")
            if chat_id and self.client:
                if action == "typing":
                    if chat_id not in self._typing_tasks:
                        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))
                elif action == "cancel":
                    task = self._typing_tasks.pop(chat_id, None)
                    if task:
                        task.cancel()
                elif action == "read":
                    try:
                        await self.client.send_read_acknowledge(chat_id)
                    except Exception as e:
                        await self.emit_log(f"Failed to send read acknowledge for {chat_id}: {e}", "ERROR")
