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
        
        # Фоновое чтение
        self.bg_read_task: Optional[asyncio.Task] = None
        
        self._load_profile()
        self._load_history()
        
        # Outbox queue
        self.outbox_queue = asyncio.Queue()
        
        # History sync state
        self.history_sync_pending = True
        self.pending_trigger_msg: Optional[UniversalMessage] = None
        
        # Дебаунсинг и очередь обработки
        self.is_llm_processing = False
        self.debounce_task: Optional[asyncio.Task] = None
        self.queued_triggers = 0
        self._had_queued_triggers = False
        
        self._start_bg_read_task()

    def _load_profile(self):
        # Load profile
        if os.path.exists(self.profile_file):
            try:
                with open(self.profile_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Merge loaded data with defaults
                    self.profile = TalkProfile(**data.get("profile", {}))
                    
                    current_settings = self.settings.model_dump()
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
                        
                    self._start_bg_read_task()
            except json.JSONDecodeError as e:
                import logging
                logging.getLogger("Symcogitant").error(f"JSON syntax error in {self.profile_file}: {e}. Profile could not be loaded!")
            except (OSError, ValueError):
                pass
                
    def _start_bg_read_task(self):
        interval = getattr(self.settings, "background_read_interval_seconds", 0)
        
        if self.bg_read_task and not self.bg_read_task.done():
            # Задача уже работает. Цикл внутри нее сам подхватит новый интервал
            # при следующей итерации или завершится, если интервал стал <= 0.
            return
            
        if interval > 0:
            self.bg_read_task = asyncio.create_task(self._background_read_loop())

    async def _background_read_loop(self):
        try:
            while True:
                interval = getattr(self.settings, "background_read_interval_seconds", 300)
                if interval <= 0:
                    break
                await asyncio.sleep(interval)
                
                if getattr(self, "running", True) is False:
                    break
                    
                if self.profile.status in [RoomMode.OBSERVER, RoomMode.FROZEN, RoomMode.DEAD]:
                    continue
                    
                if self.is_llm_processing or getattr(self, 'history_sync_pending', False):
                    continue
                    
                # Проверяем, не является ли диалог слишком старым
                ignore_days = getattr(self.settings, "background_read_ignore_older_than_days", 3)
                if ignore_days > 0 and self.messages:
                    last_msg_date = None
                    for m in reversed(self.messages):
                        if m.date:
                            last_msg_date = m.date
                            break
                    if last_msg_date:
                        try:
                            from datetime import timedelta
                            msg_dt = datetime.fromisoformat(last_msg_date.replace("Z", "+00:00"))
                            if datetime.now(timezone.utc) - msg_dt > timedelta(days=ignore_days):
                                continue
                        except Exception:
                            pass
                    
                # Добавляем в историю действие о фоновом чтении (опционально, можно не писать)
                await self.manager.emit_log(f"[{self.talk_id}] Background reading history (interval: {interval}s)", "DEBUG")
                
                await self.manager.event_bus.publish({
                    "type": "talk_mark_read",
                    "protocol": self.talk_id.split(":")[0],
                    "talk_id": self.talk_id
                })
                
                await self._trigger_llm()
        except asyncio.CancelledError:
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
            # Получаем дефолтные настройки для этого типа комнаты
            talk_defaults = self.manager.config.get("talk_defaults", {})
            base_defaults = {k: v for k, v in talk_defaults.items() if k != "by_type"}
            type_defaults = talk_defaults.get("by_type", {}).get(self.profile.talk_type, {})
            final_defaults = dict(base_defaults)
            final_defaults.update(type_defaults)
            default_settings = TalkSettings(**final_defaults)
            
            current_dump = self.settings.model_dump()
            default_dump = default_settings.model_dump()
            
            # Сохраняем ТОЛЬКО те настройки, которые отличаются от дефолтных
            settings_to_save = {k: v for k, v in current_dump.items() if k not in default_dump or default_dump[k] != v}
            
            profile_parse_error = False
            if os.path.exists(self.profile_file):
                try:
                    with open(self.profile_file, 'r', encoding='utf-8') as f:
                        disk_data = json.load(f)
                        if "settings" in disk_data:
                            # Удаляем из disk_data те ключи, которые теперь равны дефолтным (чтобы они не "застревали")
                            # и обновляем оставшимися
                            for k in list(disk_data["settings"].keys()):
                                if k not in settings_to_save:
                                    del disk_data["settings"][k]
                            disk_data["settings"].update(settings_to_save)
                            settings_to_save = disk_data["settings"]
                except json.JSONDecodeError as e:
                    import logging
                    logging.getLogger("Symcogitant").error(f"JSON syntax error in {self.profile_file}: {e}. WILL NOT OVERWRITE PROFILE TO PREVENT DATA LOSS.")
                    profile_parse_error = True
                except Exception:
                    pass

            if not profile_parse_error:
                with open(self.profile_file, 'w', encoding='utf-8') as f:
                    json.dump({
                        "profile": self.profile.model_dump(),
                        "settings": settings_to_save
                    }, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

        # Save history
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
                await self._check_triggers(msg, event_type)
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
            
            if dirty:
                await self._check_triggers(self.messages[-1], event_type)
                    
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
                # Группируем системные сообщения об удалении
                last_msg = self.messages[-1] if self.messages else None
                if last_msg and last_msg.protocol == "system" and "удал" in last_msg.text:
                    import re
                    match = re.search(r'\((\d+) шт\.\)', last_msg.text)
                    if match:
                        count = int(match.group(1)) + 1
                        last_msg.text = f"[Действие пьесы] Несколько сообщений выше были удалены ({count} шт.)."
                    else:
                        last_msg.text = f"[Действие пьесы] Несколько сообщений выше были удалены (2 шт.)."
                    last_msg.date = datetime.now(timezone.utc).isoformat()
                    dirty = True
                else:
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
                await self._check_triggers(self.messages[-1], event_type)
                
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
                await self._check_triggers(sys_msg, event_type)

        elif event_type == "talk_interlocutor_status":
            status = event.get("status")
            user_id = event.get("user_id", "unknown")
            
            # Чтобы не спамить контекст, добавляем статус "печатает" только если его не было в последних 5 сообщениях
            if status == "typing":
                recent_typing = any(m.protocol == "system" and "печатает" in m.text for m in self.messages[-5:])
                if not recent_typing:
                    sys_msg = UniversalMessage(
                        talk_id=self.talk_id,
                        msg_id=f"sys_status_{int(datetime.now(timezone.utc).timestamp()*1000)}_{user_id}",
                        protocol="system",
                        date=datetime.now(timezone.utc).isoformat(),
                        sender={"id": "system", "name": "System", "role": "admin"},
                        text=f"[Действие пьесы] Пользователь (id: {user_id}) начал печатать...",
                        is_outgoing=False
                    )
                    self.messages.append(sys_msg)
                    dirty = True
                    await self._check_triggers(sys_msg, event_type)

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

            new_unhandled_msgs = []
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
                        if sm.date:
                            try:
                                from datetime import timedelta
                                msg_dt = datetime.fromisoformat(sm.date.replace("Z", "+00:00"))
                                if datetime.now(timezone.utc) - msg_dt < timedelta(days=1):
                                    new_unhandled_msgs.append(sm)
                            except Exception:
                                pass

                self.messages.sort(key=lambda x: x.date or "")
                dirty = True
                
            self.history_sync_pending = False
            
            # Триггерим реакцию на пропущенные (пока бот был оффлайн) новые сообщения
            if synced_msgs:
                for sm in new_unhandled_msgs:
                    await self._check_triggers(sm, "talk_message_received")
                    
            if getattr(self, "pending_trigger_msg", None):
                msg = self.pending_trigger_msg
                evt = getattr(self, "pending_trigger_event", "talk_message_received")
                self.pending_trigger_msg = None
                self.pending_trigger_event = None
                await self._check_triggers(msg, evt)
                
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

    async def _check_triggers(self, latest_msg: UniversalMessage, event_type: str):
        if self.profile.status in [RoomMode.OBSERVER, RoomMode.FROZEN, RoomMode.DEAD]:
            return
            
        if getattr(self, 'history_sync_pending', False):
            self.pending_trigger_msg = latest_msg
            self.pending_trigger_event = event_type
            return

        should_reply = False
        
        trigger_events = getattr(self.settings, "trigger_events", [])
        
        if event_type in trigger_events:
            should_reply = True
        elif self.settings.trigger_on_mention and event_type == "talk_message_received" and (getattr(latest_msg, 'mentions_me', False) or self._is_mentioned(latest_msg.text)):
            should_reply = True
        elif self.settings.trigger_on_reply and event_type == "talk_message_received" and latest_msg.reply_to_msg_id:
            for m in reversed(self.messages):
                if m.msg_id == latest_msg.reply_to_msg_id:
                    if m.sender.is_me:
                        should_reply = True
                    break
        
        if should_reply:
            if self.is_llm_processing:
                self.queued_triggers += 1
                return
                
            if self.debounce_task and not self.debounce_task.done():
                self.debounce_task.cancel()
                
            self.debounce_task = asyncio.create_task(self._debounced_trigger())

    async def _debounced_trigger(self):
        try:
            import random
            delay = random.uniform(self.settings.debounce_delay_min, self.settings.debounce_delay_max)
            
            # 1. Ждем примерно 30% времени перед тем, как "прочитать" сообщение
            await asyncio.sleep(delay * 0.3)
            await self.manager.event_bus.publish({
                "type": "talk_mark_read",
                "protocol": self.talk_id.split(":")[0],
                "talk_id": self.talk_id
            })
            
            # 2. Ждем еще 40% времени и "начинаем печатать"
            await asyncio.sleep(delay * 0.4)
            await self.manager.event_bus.publish({
                "type": "talk_set_action",
                "protocol": self.talk_id.split(":")[0],
                "talk_id": self.talk_id,
                "action": "typing"
            })
            
            # 3. Ждем оставшиеся 30% и вызываем LLM
            await asyncio.sleep(delay * 0.3)
            await self._trigger_llm()
        except asyncio.CancelledError:
            pass

    def _is_mentioned(self, text: str) -> bool:
        if not text or not self.settings.mention_aliases:
            return False
        import re
        escaped_aliases = [re.escape(alias) for alias in self.settings.mention_aliases]
        pattern = r'\b(?:' + '|'.join(escaped_aliases) + r')\b'
        return bool(re.search(pattern, text, flags=re.IGNORECASE))

    async def _trigger_llm(self):
        if self.is_llm_processing:
            return
            
        self.is_llm_processing = True
        
        try:
            # 0. Динамически перезагружаем настройки из файла, чтобы подхватить изменения извне
            self._load_profile()
            
            protocol = self.talk_id.split(":")[0] if ":" in self.talk_id else "unknown"
            capabilities = self.manager.transports_capabilities.get(protocol, {})
            
            # 1. Подготовка схемы и переменных
            schema = getattr(self.settings, "context_schema", None)
            if not schema:
                system_text = f"\n\nRules for this room:\n{self.settings.system_prompt}" if self.settings.system_prompt else ""
                schema = {
                    "system_blocks": [
                        {
                            "type": "dynamic",
                            "template": "You are operating in the talk room '{title}' (Type: {talk_type}, Status: {status}).\nHere is the history of the conversation formatted as a script.\nAnalyze the context and use the available tools to respond if necessary."
                        },
                        {
                            "type": "text",
                            "text": system_text
                        }
                    ],
                    "history_blocks": {
                        "max_messages": self.settings.context_window_size
                    },
                    "postfix_blocks": [],
                    "tools": {
                        "allowed": self.settings.allowed_extra_tools
                    }
                }
                
            postfix = schema.get("postfix_blocks", [])
            if not isinstance(postfix, list):
                postfix = []
                
            if self._had_queued_triggers:
                postfix.append({
                    "type": "dynamic",
                    "template": f"[Внимание] Пока вы генерировали предыдущий ответ, пользователь прислал дополнительные сообщения ({self.queued_triggers} шт.). Пожалуйста, ответьте на них с учетом вашего предыдущего ответа."
                })
                self._had_queued_triggers = False
                self.queued_triggers = 0
                
            if self.profile.talk_type == "priv":
                postfix.append({
                    "type": "text",
                    "text": "[System] Это приватный чат с пользователем. Вы обязаны ответить на его последнее сообщение."
                })
                
            schema["postfix_blocks"] = postfix
                
            variables = {
                "title": self.profile.title,
                "talk_type": self.profile.talk_type,
                "status": self.profile.status.value
            }
            
            # 2. Подготовка сообщений
            raw_messages = [msg.model_dump(mode='json', exclude_none=True) for msg in self.messages]
            
            request_id = f"req_{self.safe_id}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
            
            # 3. Отправка запроса на сборку в шину
            request_data = {
                "type": "context_assembly_request",
                "request_id": request_id,
                "talk_id": self.talk_id,
                "messages": raw_messages,
                "schema": schema,
                "variables": variables,
                "tools_capabilities": capabilities,
                "routing": {
                    "tier": getattr(self.settings, "preferred_model_tier", "smart"),
                    "primary_model": getattr(self.settings, "primary_model", ""),
                    "fallback_model": getattr(self.settings, "fallback_model", "")
                }
            }
            
            await self.manager.event_bus.publish(request_data)
            # Сохраняем привязку request_id -> talk_id в менеджере
            self.manager.pending_requests[request_id] = self.talk_id
            await self.manager.emit_log(f"Sent context assembly request for {self.talk_id}", "INFO")
            
        except Exception as e:
            self.is_llm_processing = False
            import traceback
            err_str = traceback.format_exc()
            await self.manager.emit_log(f"Error in _trigger_llm for {self.talk_id}: {e}\n{err_str}", "ERROR")

    async def handle_llm_response(self, event: dict):
        self.is_llm_processing = False
        
        # Отменяем статус "печатает"
        await self.manager.event_bus.publish({
            "type": "talk_set_action",
            "protocol": self.talk_id.split(":")[0],
            "talk_id": self.talk_id,
            "action": "cancel"
        })
        
        if event.get("type") == "llm_response_error":
            error_msg = event.get("error", "Unknown error")
            await self.manager.emit_log(f"LLM Error in {self.talk_id}: {error_msg}", "ERROR")
            self._check_post_llm_queue()
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
            self._check_post_llm_queue()
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

        self._check_post_llm_queue()

    def _check_post_llm_queue(self):
        if self.queued_triggers > 0:
            self._had_queued_triggers = True
            if self.debounce_task and not self.debounce_task.done():
                self.debounce_task.cancel()
            self.debounce_task = asyncio.create_task(self._debounced_trigger())
