import asyncio
import json
import os
import re
from typing import List, Optional, Any
from datetime import datetime, timezone

from src.core.base import BaseAgentPlugin
from .models import UniversalMessage, TalkProfile, TalkSettings, RoomMode

class Talk:
    def __init__(self, talk_id: str, manager_plugin: Any, config: dict):
        self.talk_id = talk_id
        self.manager = manager_plugin
        self.config = config
        
        self.safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', talk_id)
        
        parts = talk_id.split(":")
        protocol = parts[0] if len(parts) > 0 else "unknown"
        talk_type = parts[1] if len(parts) > 1 else "unknown"
        target_id = parts[2] if len(parts) > 2 else "unknown"
        # Для форумов (tg:forum:-100:topic:42) можно сделать вложенность
        topic_suffix = f"/topic_{parts[4]}" if len(parts) > 4 and parts[3] == "topic" else ""
        
        self.talks_dir = os.path.join(os.getcwd(), "data", "talks", protocol, talk_type, f"{target_id}{topic_suffix}")
        os.makedirs(self.talks_dir, exist_ok=True)
        
        self.history_file = os.path.join(self.talks_dir, "history.jsonl")
        self.profile_file = os.path.join(self.talks_dir, "profile.json")
        
        self.messages: List[UniversalMessage] = []
        
        talk_defaults = self.manager.config.get("talk_defaults", {})
        base_defaults = {k: v for k, v in talk_defaults.items() if k != "by_type"}
        type_defaults = talk_defaults.get("by_type", {}).get(talk_type, {})
        
        final_defaults = dict(base_defaults)
        final_defaults.update(type_defaults)
        
        self.profile = TalkProfile(
            created_at=datetime.now(timezone.utc).isoformat(),
            talk_type=talk_type
        )
        self.settings = TalkSettings(**final_defaults)
        
        self._load_profile()
        self._load_history()
        
        # Outbox queue
        self.outbox_queue = asyncio.Queue()
        
        # History sync state
        self.history_sync_pending = True
        self.pending_trigger_msg: Optional[UniversalMessage] = None

    def _load_profile(self):
        # Load profile
        if os.path.exists(self.profile_file):
            try:
                with open(self.profile_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Merge loaded data with defaults
                    self.profile = TalkProfile(**data.get("profile", {}))
                    
                    current_settings = self.settings.model_dump() if hasattr(self.settings, 'model_dump') else self.settings.dict()
                    saved_settings = data.get("settings", {})
                    for k, v in saved_settings.items():
                        current_settings[k] = v
                    self.settings = TalkSettings(**current_settings)
                    
                    # Синхронизируем talk_type для старых профилей
                    parts = self.talk_id.split(":")
                    real_type = parts[1] if len(parts) > 1 else "unknown"
                    if getattr(self.profile, "talk_type", "unknown") != real_type:
                        self.profile.talk_type = real_type
                        self._save_data()
            except (OSError, ValueError, json.JSONDecodeError):
                pass

    def _load_history(self):
        self.messages.clear()
        # Load history
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            try:
                                msg_data = json.loads(line)
                                if msg_data.get("sender", {}).get("is_me"):
                                    text = msg_data.get("text", "")
                                    if "\n\n🤖 " in text:
                                        msg_data["text"] = text.split("\n\n🤖 ")[0].strip()
                                self.messages.append(UniversalMessage(**msg_data))
                            except (ValueError, json.JSONDecodeError, KeyError):
                                pass
            except OSError:
                pass

    def _save_data(self):
        # Save profile
        try:
            with open(self.profile_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "profile": self.profile.model_dump(),
                    "settings": self.settings.model_dump()
                }, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

        # Save history (append only approach isn't fully safe with edits/deletes,
        # so for simplicity we rewrite the file if window isn't huge.
        # A more robust solution uses a local DB, but JSONL rewrite is okay for 100-200 lines).
        try:
            # Keep only the window size
            limit = self.settings.context_window_size * 2
            if len(self.messages) > limit:
                self.messages = self.messages[-limit:]
                
            with open(self.history_file, 'w', encoding='utf-8') as f:
                for msg in self.messages:
                    f.write(msg.model_dump_json(exclude_none=True) + "\n")
        except OSError:
            pass

    async def handle_event(self, event_type: str, event: dict):
        dirty = False
        
        if event_type == "talk_message_received":
            try:
                # Обновление имени чата
                chat_info = event.get("chat_info", {})
                if chat_info.get("title") and self.profile.title == "Unknown Talk":
                    self.profile.title = chat_info["title"]
                    dirty = True
                    
                msg = UniversalMessage(**event.get("message", {}))
                self.messages.append(msg)
                dirty = True
                await self._check_triggers(msg)
            except Exception as e:
                await self.manager.emit_log(f"Talk {self.talk_id} failed to parse msg: {e}", "ERROR")
                
        elif event_type == "talk_message_edited":
            msg_id = event.get("msg_id")
            new_text = event.get("text")
            edited_date = event.get("edited_date")
            for m in reversed(self.messages):
                if m.msg_id == msg_id:
                    if new_text is not None:
                        m.text = new_text
                    m.is_edited = True
                    m.edited_date = edited_date
                    dirty = True
                    break
                    
        elif event_type == "talk_message_deleted":
            msg_id = event.get("msg_id")
            found = False
            for m in reversed(self.messages):
                if m.msg_id == msg_id:
                    if not m.is_deleted:
                        m.is_deleted = True
                        found = True
                        dirty = True
                    break
                    
            if found:
                sys_msg = UniversalMessage(
                    talk_id=self.talk_id,
                    msg_id=f"sys_del_{int(datetime.now(timezone.utc).timestamp()*1000)}_{msg_id}",
                    protocol="system",
                    date=datetime.now(timezone.utc).isoformat(),
                    sender={"id": "system", "name": "System", "role": "admin"},
                    text=f"[Действие пьесы] Одно из сообщений выше было удалено.",
                    is_outgoing=False
                )
                self.messages.append(sys_msg)
                await self._check_triggers(sys_msg)
                
        elif event_type == "talk_reaction_changed":
            # For simplicity, replace all reactions for the message
            msg_id = event.get("msg_id")
            reactions = event.get("reactions", [])
            from .models import Reaction
            
            found = False
            reacts_str = ""
            for m in reversed(self.messages):
                if m.msg_id == msg_id:
                    new_reactions = [Reaction(**r) for r in reactions]
                    if str(m.reactions) != str(new_reactions):
                        m.reactions = new_reactions
                        found = True
                        dirty = True
                        if reactions:
                            reacts_str = ", ".join(f"{r.get('emoji', '')}" for r in reactions)
                        else:
                            reacts_str = "все реакции удалены"
                    break
                    
            if found:
                sys_msg = UniversalMessage(
                    talk_id=self.talk_id,
                    msg_id=f"sys_react_{int(datetime.now(timezone.utc).timestamp()*1000)}_{msg_id}",
                    protocol="system",
                    date=datetime.now(timezone.utc).isoformat(),
                    sender={"id": "system", "name": "System", "role": "admin"},
                    text=f"[Действие пьесы] Пользователь изменил реакции на одно из сообщений. Текущие реакции: {reacts_str}",
                    is_outgoing=False
                )
                self.messages.append(sys_msg)
                await self._check_triggers(sys_msg)

        elif event_type == "talk_history_sync_response":
            raw_msgs = event.get("messages", [])
            synced_msgs = []
            for rm in raw_msgs:
                try:
                    if rm.get("sender", {}).get("is_me"):
                        text = rm.get("text", "")
                        if "\n\n🤖 " in text:
                            rm["text"] = text.split("\n\n🤖 ")[0].strip()
                    synced_msgs.append(UniversalMessage(**rm))
                except ValueError:
                    pass

            if synced_msgs:
                synced_msg_ids = {sm.msg_id for sm in synced_msgs if sm.msg_id}
                oldest_synced_date = min([sm.date for sm in synced_msgs if sm.date] or [""])
                
                # Smart diff: drop messages that exist locally, are in the synced timeframe,
                # but missing from synced_msgs (meaning they were deleted on server).
                self.messages = [
                    m for m in self.messages 
                    if not m.date or m.date < oldest_synced_date or not m.msg_id or m.msg_id in synced_msg_ids
                ]
                
                for sm in synced_msgs:
                    found = False
                    for m in self.messages:
                        if m.msg_id == sm.msg_id:
                            # Update existing
                            m.text = sm.text
                            m.reactions = sm.reactions
                            m.is_edited = sm.is_edited
                            m.edited_date = sm.edited_date
                            m.is_deleted = sm.is_deleted
                            found = True
                            break
                    if not found:
                        self.messages.append(sm)

                self.messages.sort(key=lambda x: x.date or "")
                dirty = True
                
            self.history_sync_pending = False
            if getattr(self, "pending_trigger_msg", None):
                msg = self.pending_trigger_msg
                self.pending_trigger_msg = None
                await self._check_triggers(msg)
                
        elif event_type == "talk_message_sent":
            try:
                msg = UniversalMessage(**event.get("message", {}))
                
                if msg.sender.is_me:
                    if "\n\n🤖 " in msg.text:
                        msg.text = msg.text.split("\n\n🤖 ")[0].strip()
                    
                self.messages.append(msg)
                dirty = True
            except ValueError:
                pass
                
        if dirty:
            self._save_data()

    async def _check_triggers(self, latest_msg: UniversalMessage):
        if self.profile.status in [RoomMode.OBSERVER, RoomMode.FROZEN, RoomMode.DEAD]:
            return
            
        if self.history_sync_pending:
            self.pending_trigger_msg = latest_msg
            return

        should_reply = False
        
        if self.settings.trigger_on_every_message:
            should_reply = True
        elif self.settings.trigger_on_mention and (getattr(latest_msg, 'mentions_me', False) or self._is_mentioned(latest_msg.text)):
            should_reply = True
        elif self.settings.trigger_on_reply and latest_msg.reply_to_msg_id:
            for m in reversed(self.messages):
                if m.msg_id == latest_msg.reply_to_msg_id:
                    if m.sender.is_me:
                        should_reply = True
                    break
        
        if should_reply:
            await self._trigger_llm()

    def _is_mentioned(self, text: str) -> bool:
        if not text or not self.settings.mention_aliases:
            return False
        import re
        escaped_aliases = [re.escape(alias) for alias in self.settings.mention_aliases]
        pattern = r'\b(?:' + '|'.join(escaped_aliases) + r')\b'
        return bool(re.search(pattern, text, flags=re.IGNORECASE))

    async def _trigger_llm(self):
        # 0. Динамически перезагружаем настройки из файла, чтобы подхватить изменения извне
        self._load_profile()
        
        # 1. Собрать контекст
        messages_to_process = self.messages[-self.settings.context_window_size:]
        script_parts = []
        for msg in messages_to_process:
            sender_name = msg.sender.name
            role_label = "Assistant (Me)" if msg.sender.is_me else f"{msg.sender.role.value.capitalize()} ({sender_name})"
            
            status_flags = []
            if msg.is_deleted:
                status_flags.append("[DELETED]")
            if msg.is_edited:
                status_flags.append(f"[EDITED at {msg.edited_date}]")
            
            flags_str = " ".join(status_flags)
            if flags_str:
                flags_str = " " + flags_str
                
            time_str = f"[{msg.date}] " if msg.date else ""
            
            # Реакции
            reactions_str = ""
            if msg.reactions:
                reacts = ", ".join(f"{r.user_id}: {r.emoji}" for r in msg.reactions)
                reactions_str = f"\n[Reactions: {reacts}]"
            
            script_parts.append(f"{time_str}{role_label}{flags_str}:\n{msg.text}{reactions_str}")

        full_context = "\n\n".join(script_parts)
        
        # 2. Определить инструменты
        # Извлекаем протокол из talk_id (например, "tg:priv:123" -> "tg")
        protocol = self.talk_id.split(":")[0] if ":" in self.talk_id else "unknown"
        capabilities = self.manager.transports_capabilities.get(protocol, {})
        
        tools_list = []
        # Базовые инструменты, зависящие от возможностей транспорта
        if capabilities.get("can_send_text", False):
            tools_list.append({
                "type": "function",
                "function": {
                    "name": "reply_to_talk",
                    "description": "Send a reply message back to the current chat room.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "The text of the message to send."
                            },
                            "reply_to_msg_id": {
                                "type": "string",
                                "description": "Optional message ID to reply to."
                            }
                        },
                        "required": ["text"]
                    }
                }
            })
        
        # Добавляем дополнительные разрешенные инструменты для комнаты
        if hasattr(self.manager, "core") and self.manager.core and hasattr(self.manager.core, "tools_registry") and self.manager.core.tools_registry:
            all_schemas = self.manager.core.tools_registry.get_tools_schema()
            if all_schemas:
                for tool_name in self.settings.allowed_extra_tools:
                    for schema in all_schemas:
                        if schema.get("type") == "function" and schema.get("function", {}).get("name") == tool_name:
                            tools_list.append(schema)
        
        # 3. Отправка запроса в шину
        request_id = f"req_{self.safe_id}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        
        system_prompt = (
            f"You are operating in the talk room '{self.profile.title}' (Type: {self.profile.talk_type}, Status: {self.profile.status.value}).\n"
            f"Here is the history of the conversation formatted as a script.\n"
            f"Analyze the context and use the available tools to respond if necessary."
        )
        
        if self.settings.system_prompt:
            system_prompt += f"\n\nRules for this room:\n{self.settings.system_prompt}"
        
        request_data = {
            "type": "llm_route_request",
            "request_id": request_id,
            "talk_id": self.talk_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": full_context}
            ],
            "tools": tools_list,
            "tier": getattr(self.settings, "preferred_model_tier", "smart"),
            "primary_model": getattr(self.settings, "primary_model", ""),
            "fallback_model": getattr(self.settings, "fallback_model", "")
        }
        
        await self.manager.event_bus.publish(request_data)
        # Сохраняем привязку request_id -> talk_id в менеджере
        self.manager.pending_requests[request_id] = self.talk_id
        await self.manager.emit_log(f"Triggered LLM for {self.talk_id} with {len(messages_to_process)} msgs", "INFO")

    async def handle_llm_response(self, event: dict):
        if event.get("type") == "llm_response_error":
            error_msg = event.get("error", "Unknown error")
            await self.manager.emit_log(f"LLM Error in {self.talk_id}: {error_msg}", "ERROR")
            return
            
        reply_text = event.get("reply", "").strip()
        tool_calls = event.get("tool_calls", [])
        
        signature = ""
        if getattr(self.settings, "append_llm_signature", False):
            usage = event.get("usage", {})
            
            # Воркер передает model и response_model
            model_str = event.get("model", "unknown")
            key_name = event.get("key_name", "default")
            
            if "/" in model_str:
                provider, req_model = model_str.split("/", 1)
            else:
                provider, req_model = "local", model_str
                
            if key_name and key_name != "default":
                req_model += f"#{key_name}"
                
            act_model = event.get("response_model", req_model)
            if "/" in act_model:
                act_model = act_model.split("/", 1)[-1]
            
            in_t = usage.get("prompt_tokens", 0)
            out_t = usage.get("completion_tokens", 0)
            cache_t = usage.get("cache_read_tokens", 0)
            cache_pct = int((cache_t / in_t * 100)) if in_t > 0 else 0
            
            template = getattr(self.settings, "llm_signature_template", "\n\n🤖 {provider}>{req_model} || {act_model} [in/cash_%/out - {in_t} / {cache_pct}% / {out_t} tokens].")
            try:
                signature = template.format(
                    provider=provider,
                    req_model=req_model,
                    act_model=act_model,
                    in_t=in_t,
                    cache_pct=cache_pct,
                    out_t=out_t
                )
            except Exception:
                signature = f"\n\n🤖 {provider}>{req_model} || {act_model} [in/cash_%/out - {in_t} / {cache_pct}% / {out_t} tokens]."
        
        # Если модель ответила простым текстом без вызова reply_to_talk
        has_reply_tool = any(tc.get("function", tc).get("name") == "reply_to_talk" for tc in tool_calls)
        if reply_text and not has_reply_tool:
            out_msg_event = {
                "type": "talk_send_message",
                "protocol": self.talk_id.split(":")[0],
                "talk_id": self.talk_id,
                "text": reply_text + signature,
                "reply_to_msg_id": None
            }
            await self.manager.event_bus.publish(out_msg_event)
            await self.manager.emit_log(f"Sending native reply to {self.talk_id} via {out_msg_event['protocol']}", "INFO")
            
            if self.messages:
                self.messages[-1].internal_metadata["llm_responded_at"] = datetime.now(timezone.utc).isoformat()
                self._save_data()
                
        if not tool_calls:
            # Нет вызовов инструментов для дальнейшей обработки
            return
            
        for call in tool_calls:
            func = call.get("function", call)
            name = func.get("name")
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            
            if name == "reply_to_talk":
                text = args.get("text", "")
                reply_to_id = args.get("reply_to_msg_id")
                
                # Помещаем в Outbox очередь (в этой версии мы напрямую шлем в шину)
                # Идемпотентная отправка будет реализована на уровне транспорта или продвинутого Outbox
                out_msg_event = {
                    "type": "talk_send_message",
                    "protocol": self.talk_id.split(":")[0],
                    "talk_id": self.talk_id,
                    "text": text + signature,
                    "reply_to_msg_id": reply_to_id
                }
                await self.manager.event_bus.publish(out_msg_event)
                await self.manager.emit_log(f"Sending message to {self.talk_id} via {out_msg_event['protocol']}", "INFO")
                
                # Добавляем "прочитано" / "ответ сформирован" в internal_metadata последнего сообщения
                if self.messages:
                    self.messages[-1].internal_metadata["llm_responded_at"] = datetime.now(timezone.utc).isoformat()
                    self._save_data()
            
            elif name in self.settings.allowed_extra_tools:
                # Если вызван дополнительный тулз, мы перенаправляем его запрос в шину (например, mcp_request)
                pass
