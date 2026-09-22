import asyncio
import json
import os
import time
import re
import random

class GroupDialogSession:
    def __init__(self, chat_id: int, dispatcher, config: dict):
        self.chat_id = chat_id
        self.dispatcher = dispatcher
        self.config = config
        
        self.incoming_queue = asyncio.Queue()
        self.llm_resp_queue = asyncio.Queue()
        self.wakeup_event = asyncio.Event()
        
        self.timeout_sec = self.config.get("telegram_group_dispatcher", {}).get("session_timeout_seconds", 1000)
        self.window_size = self.config.get("telegram_group_dispatcher", {}).get("max_context_messages", 100)
        self.default_model = self.config.get("telegram_group_dispatcher", {}).get("default_model", "hf.co/unsloth/rnj-1-instruct-GGUF:Q4_K_M")
        
        self.history_dir = os.path.join(os.getcwd(), "data", "talks", "telegram_group")
        os.makedirs(self.history_dir, exist_ok=True)
        self.session_file = os.path.join(self.history_dir, f"{self.chat_id}.jsonl")
        
        self.messages = []
        self._load_history()
        
    def _load_history(self):
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            self.messages.append(json.loads(line))
            except Exception as e:
                pass

    def _append_to_history(self, msg: dict):
        self.messages.append(msg)
        if self.window_size > 0 and len(self.messages) > self.window_size:
            self.messages = self.messages[-self.window_size:]
            
        try:
            with open(self.session_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _get_system_prompt(self) -> str:
        base_path = os.path.join(os.getcwd(), "data", "memory", "selfhood")
        files = ["kernel_identity.md", "cognitive_map.md", "runtime_state.md"]
        prompt = ""
        for f in files:
            path = os.path.join(base_path, f)
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as fd:
                        prompt += fd.read() + "\n\n"
                except Exception:
                    pass
        return prompt.strip() if prompt else "You are an AI assistant."

    def _has_trigger_words(self, evt: dict) -> bool:
        text = evt.get("text", "")
        if not text:
            return False
            
        if evt.get("is_reply_to_me") or evt.get("mentions_me"):
            return True
            
        text_lower = text.lower()
        # Если это прямая ссылка на сообщение в закрытой группе (t.me/c/...), 
        # мы считаем это обращением, если там есть юзернейм zeleny_ai (или любой другой триггер).
        # Но основную логику мы уже обработали в is_reply_to_me и mentions_me.
        if "t.me/c/" in text_lower and "zeleny_ai" in text_lower:
             return True
             
        return bool(re.search(r'\b(зеленый|зелёный|ии)\b', text_lower))

    async def run(self):
        try:
            while True:
                await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: waiting for messages...", "INFO")
                msg_event = await asyncio.wait_for(self.incoming_queue.get(), timeout=self.timeout_sec)
                
                # Собираем все сообщения из очереди
                events = [msg_event]
                while not self.incoming_queue.empty():
                    events.append(self.incoming_queue.get_nowait())
                    
                triggered = False
                for evt in events:
                    text = evt.get("text", "")
                    sender = evt.get("sender", "Unknown")
                    if not text:
                        continue
                        
                    formatted_msg = f"[User: {sender}] {text}"
                    self._append_to_history({"role": "user", "content": formatted_msg})
                    
                    if self._has_trigger_words(evt):
                        triggered = True

                if not triggered:
                    continue
                    
                # Если сработал триггер, начинаем цикл обработки ответа
                await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: Trigger word detected!", "INFO")
                
                # Задержка 2-4 секунды перед send_read_acknowledge
                await asyncio.sleep(random.uniform(2.0, 4.0))
                await self.dispatcher.event_bus.publish({
                    "type": "telegram_chat_action",
                    "source": self.dispatcher.__class__.__name__,
                    "chat_id": self.chat_id,
                    "action": "read"
                })
                
                # Задержка 1-2 секунды перед typing
                await asyncio.sleep(random.uniform(1.0, 2.0))
                await self.dispatcher.event_bus.publish({
                    "type": "telegram_chat_action",
                    "source": self.dispatcher.__class__.__name__,
                    "chat_id": self.chat_id,
                    "action": "typing"
                })
                
                # Подготавливаем запрос к LLM
                sys_prompt = self._get_system_prompt()
                context_msgs = [{"role": "system", "content": sys_prompt}]
                
                recent_msgs = self.messages[-self.window_size:] if self.window_size > 0 else self.messages
                for m in recent_msgs:
                    if m.get("role") != "system":
                        context_msgs.append({"role": m["role"], "content": m["content"]})
                        
                while True:
                    # Запрашиваем модель у диспетчера
                    model_info = self.dispatcher.get_llm_model({"chat_id": self.chat_id})
                    if not model_info:
                        await self.dispatcher.emit_log(f"Session {self.chat_id}: no model available, pausing response.", "WARNING")
                        await self.wakeup_event.wait()
                        self.wakeup_event.clear()
                        continue

                    model = model_info.get("model")
                    api_key_name = model_info.get("api_key_name")

                    req_id = f"tg_grp_{self.chat_id}_{int(time.time()*1000)}"
                    request_data = {
                        "type": "llm_request",
                        "request_id": req_id,
                        "model": model,
                        "messages": context_msgs
                    }
                    if api_key_name:
                        request_data["api_key_name"] = api_key_name
                    
                    await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: sending llm_request {req_id}", "INFO")
                    await self.dispatcher.send_llm_request(req_id, self.chat_id, request_data)
                    
                    await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: waiting for llm_response...", "INFO")
                    llm_event = await self.llm_resp_queue.get()
                    if llm_event.get("request_id") != req_id:
                        continue
                    
                    if llm_event.get("type") == "llm_response":
                        reply = llm_event.get("reply", "")
                        if reply:
                            if "[NO ANSWER]" in reply:
                                await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: ignored message due to [NO ANSWER]", "INFO")
                                await self.dispatcher.event_bus.publish({"type": "telegram_chat_action", "source": self.dispatcher.__class__.__name__, "chat_id": self.chat_id, "action": "cancel"})
                            else:
                                self._append_to_history({"role": "assistant", "content": reply})
                                key_suffix = f"#{llm_event.get('key_name')}" if llm_event.get('key_name') and llm_event.get('key_name') != "default" else ""
                                actual_model = llm_event.get("response_model", model)
                                provider = model.split("/")[0] if "/" in model else ""
                                display_model = actual_model
                                if provider and actual_model.startswith(f"{provider}/"):
                                    display_model = actual_model[len(provider)+1:]
                                signature_model = f"{provider}>{display_model}" if provider else display_model
                                tokens = llm_event.get("usage", {}).get("total_tokens", 0)
                                telegram_reply = reply + f"\n\n---\n🤖 `{signature_model}{key_suffix} [{tokens} tokens]`"
                                await self.dispatcher.send_telegram_message(self.chat_id, telegram_reply)
                                await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: sent reply", "INFO")
                        break
                    elif llm_event.get("type") == "llm_response_error":
                        error = llm_event.get("error", "Unknown LLM error")
                        self.dispatcher.report_llm_error(model, error)
                        error_msg = f"[System Error] {error}\nПереключаюсь на другую модель..."
                        self._append_to_history({"role": "assistant", "content": error_msg})
                        await self.dispatcher.send_telegram_message(self.chat_id, error_msg)
                        await self.dispatcher.event_bus.publish({"type": "telegram_chat_action", "source": self.dispatcher.__class__.__name__, "chat_id": self.chat_id, "action": "cancel"})
                        continue
                    
        except asyncio.TimeoutError:
            await self.dispatcher.emit_log(f"GroupSession {self.chat_id} timed out.", "INFO")
            await self.dispatcher.event_bus.publish({"type": "telegram_chat_action", "source": self.dispatcher.__class__.__name__, "chat_id": self.chat_id, "action": "cancel"})
            await self.dispatcher.close_session(self.chat_id)
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            await self.dispatcher.emit_log(f"GroupSession {self.chat_id} crashed: {e}\n{err}", "ERROR")
            await self.dispatcher.close_session(self.chat_id)
