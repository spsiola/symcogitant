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

    def _has_trigger_words(self, text: str) -> bool:
        if not text:
            return False
        text_lower = text.lower()
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
                    
                    if self._has_trigger_words(text):
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
                        
                req_id = f"tg_grp_{self.chat_id}_{int(time.time()*1000)}"
                request_data = {
                    "type": "llm_request",
                    "request_id": req_id,
                    "model": self.default_model,
                    "messages": context_msgs
                }
                
                await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: sending llm_request {req_id}", "INFO")
                await self.dispatcher.send_llm_request(req_id, self.chat_id, request_data)
                
                await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: waiting for llm_response...", "INFO")
                llm_event = await self.llm_resp_queue.get()
                
                if llm_event.get("type") == "llm_response":
                    reply = llm_event.get("reply", "")
                    if reply:
                        self._append_to_history({"role": "assistant", "content": reply})
                        await self.dispatcher.send_telegram_message(self.chat_id, reply)
                        await self.dispatcher.emit_log(f"GroupSession {self.chat_id}: sent reply", "INFO")
                elif llm_event.get("type") == "llm_response_error":
                    error = llm_event.get("error", "Unknown LLM error")
                    await self.dispatcher.send_telegram_message(self.chat_id, f"[System Error] {error}")
                    
        except asyncio.TimeoutError:
            await self.dispatcher.emit_log(f"GroupSession {self.chat_id} timed out.", "INFO")
            await self.dispatcher.close_session(self.chat_id)
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            await self.dispatcher.emit_log(f"GroupSession {self.chat_id} crashed: {e}\n{err}", "ERROR")
            await self.dispatcher.close_session(self.chat_id)
