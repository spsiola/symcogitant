import asyncio
import json
import os
import time

class PrivateDialogSession:
    def __init__(self, chat_id: int, dispatcher, config: dict):
        self.chat_id = chat_id
        self.dispatcher = dispatcher
        self.config = config
        
        self.incoming_queue = asyncio.Queue()
        self.llm_resp_queue = asyncio.Queue()
        self.wakeup_event = asyncio.Event()
        
        self.timeout_sec = self.config.get("timeout_sec", 1000)
        self.window_size = self.config.get("window_size", 100)
        self.default_model = self.config.get("default_model", "hf.co/unsloth/rnj-1-instruct-GGUF:Q4_K_M")
        
        self.history_dir = os.path.join(os.getcwd(), "data", "talks", "telegram_private")
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

    async def run(self):
        try:
            while True:
                # Ждем первого сообщения, если тишина больше timeout_sec, завершаем сессию
                await self.dispatcher.emit_log(f"Session {self.chat_id}: waiting for user message...", "INFO")
                msg_event = await asyncio.wait_for(self.incoming_queue.get(), timeout=self.timeout_sec)
                
                # Аккумулируем все сообщения, которые пришли подряд
                user_texts = [msg_event.get("text")]
                while not self.incoming_queue.empty():
                    evt = await self.incoming_queue.get_nowait()
                    user_texts.append(evt.get("text"))
                    
                combined_text = "\n\n".join(filter(None, user_texts))
                await self.dispatcher.emit_log(f"Session {self.chat_id}: received text '{combined_text}'", "INFO")
                if not combined_text:
                    continue
                    
                self._append_to_history({"role": "user", "content": combined_text})
                
                # Подготавливаем запрос к LLM
                sys_prompt = self._get_system_prompt()
                
                context_msgs = [{"role": "system", "content": sys_prompt}]
                
                # Скользящее окно из последних N сообщений
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

                    req_id = f"tg_priv_{self.chat_id}_{int(time.time()*1000)}"
                    request_data = {
                        "type": "llm_request",
                        "request_id": req_id,
                        "model": model,
                        "messages": context_msgs
                    }
                    if api_key_name:
                        request_data["api_key_name"] = api_key_name
                    
                    # Отправляем запрос
                    await self.dispatcher.emit_log(f"Session {self.chat_id}: sending llm_request {req_id}", "INFO")
                
                    import random
                    # Задержка перед "прочитано"
                    await asyncio.sleep(random.uniform(1.0, 3.0))
                    await self.dispatcher.event_bus.publish({
                        "type": "telegram_chat_action",
                        "source": self.dispatcher.__class__.__name__,
                        "chat_id": self.chat_id,
                        "action": "read"
                    })
                    
                    await self.dispatcher.event_bus.publish({
                        "type": "telegram_chat_action",
                        "source": self.dispatcher.__class__.__name__,
                        "chat_id": self.chat_id,
                        "action": "typing"
                    })
                    
                    await self.dispatcher.send_llm_request(req_id, self.chat_id, request_data)
                    
                    # Ждем ответ. Lock не нужен, так как мы блокируем цикл while
                    # Следующие входящие сообщения от пользователя будут лежать в incoming_queue 
                    # и обработаются на следующей итерации (причем вместе, через внутренний while)
                    await self.dispatcher.emit_log(f"Session {self.chat_id}: waiting for llm_response...", "INFO")
                    llm_event = await self.llm_resp_queue.get()
                    if llm_event.get("request_id") != req_id:
                        continue
                    await self.dispatcher.emit_log(f"Session {self.chat_id}: got llm_event: {llm_event.get('type')}", "INFO")
                    
                    if llm_event.get("type") == "llm_response":
                        reply = llm_event.get("reply", "")
                        if reply:
                            if "[NO ANSWER]" in reply:
                                await self.dispatcher.emit_log(f"Session {self.chat_id}: ignored message due to [NO ANSWER]", "INFO")
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
                                await self.dispatcher.emit_log(f"Session {self.chat_id}: sent reply to telegram", "INFO")
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
            # Сессия завершается по таймауту неактивности
            await self.dispatcher.emit_log(f"Session {self.chat_id} timed out.", "INFO")
            await self.dispatcher.event_bus.publish({"type": "telegram_chat_action", "source": self.dispatcher.__class__.__name__, "chat_id": self.chat_id, "action": "cancel"})
            await self.dispatcher.close_session(self.chat_id)
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            await self.dispatcher.emit_log(f"Session {self.chat_id} crashed: {e}\n{err}", "ERROR")
            await self.dispatcher.event_bus.publish({"type": "telegram_chat_action", "source": self.dispatcher.__class__.__name__, "chat_id": self.chat_id, "action": "cancel"})
            await self.dispatcher.close_session(self.chat_id)
