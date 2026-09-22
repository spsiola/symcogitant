import asyncio
import json
import os
import time
from typing import Any, Dict

from src.core.base import BasePlugin

class LLMChatPlugin(BasePlugin):
    """
    Плагин прямого чата с LLM.
    Хранит историю сообщений, отправляет запросы к LLMReflex и пересылает
    ответы обратно в UI.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Интерфейс для прямого общения с LLM моделями через WebUI."
        self.history_dir = os.path.join(os.getcwd(), "data", "logs", "LLMChatPlugin")
        self.session_file = os.path.join(self.history_dir, "chat_history.json")
        self.messages = []
        
        # Создаем директорию для логов, если её нет
        os.makedirs(self.history_dir, exist_ok=True)
        self._load_history()

    def _load_history(self):
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'r', encoding='utf-8') as f:
                    self.messages = json.load(f)
            except Exception as e:
                self.messages = []
        else:
            self.messages = [
                {"role": "system", "content": "You are a helpful AI assistant inside the Symcogitant system."}
            ]
            self._save_history()

    def _save_history(self):
        try:
            with open(self.session_file, 'w', encoding='utf-8') as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    async def run(self):
        self.event_bus.subscribe(self._handle_event)
        
    async def _send_history_to_ui(self):
        await self.event_bus.publish({
            "type": "ui_chat_system",
            "source": self.__class__.__name__,
            "message": f"Плагин запущен. История загружена ({len(self.messages)} сообщений)."
        })
        
        if hasattr(self.core, "tools_registry") and self.core.tools_registry:
            tools_schema = self.core.tools_registry.get_tools_schema()
            if tools_schema:
                tool_names = [t["function"]["name"] for t in tools_schema if t.get("type") == "function"]
                await self.event_bus.publish({
                    "type": "ui_chat_system",
                    "source": self.__class__.__name__,
                    "message": f"Доступные инструменты (tools): {', '.join(tool_names)}"
                })
        
        # Загружаем предыдущие сообщения в UI (включая системный промпт)
        for msg in self.messages:
            event_data = {
                "type": "ui_chat_output",
                "source": self.__class__.__name__,
                "role": msg["role"],
                "content": msg["content"]
            }
            if "metadata" in msg:
                event_data["metadata"] = msg["metadata"]
            await self.event_bus.publish(event_data)

    async def run(self):
        self.event_bus.subscribe(self._handle_event)
        
        # Отправляем системное сообщение в UI о том, что плагин загрузился и история загружена
        await asyncio.sleep(1) # Ждем пока UI прогрузится
        await self._send_history_to_ui()
        
        while self.running:
            await asyncio.sleep(1)

    async def _handle_event(self, event: dict):
        if event.get("type") == "ui_client_connected":
            await self._send_history_to_ui()
            
        elif event.get("type") == "ui_chat_input" and event.get("target") == self.__class__.__name__:
            # Пользователь прислал сообщение
            user_text = event.get("message")
            if not user_text:
                return
                
            self.messages.append({"role": "user", "content": user_text})
            self._save_history()
            self.tool_iteration_count = 0
            
            # Отправляем системное уведомление в чат, что запрос ушел
            await self.event_bus.publish({
                "type": "ui_chat_system",
                "source": self.__class__.__name__,
                "message": "Запрос отправлен в LLMReflex..."
            })
            
            # Формируем запрос для LLMReflex
            req_id = f"chat_{int(time.time()*1000)}"
            request_data = {
                "type": "llm_request",
                "request_id": req_id,
                "messages": self.messages.copy()
            }
            if event.get("model"):
                request_data["model"] = event.get("model")
                self.last_llm_model = event.get("model")
            if event.get("num_ctx"):
                request_data["num_ctx"] = event.get("num_ctx")
                self.last_num_ctx = event.get("num_ctx")
            if event.get("api_key_name"):
                request_data["api_key_name"] = event.get("api_key_name")
                self.last_api_key_name = event.get("api_key_name")
            
            # Прокидываем все инструменты, если реестр доступен
            if hasattr(self.core, "tools_registry") and self.core.tools_registry:
                tools_schema = self.core.tools_registry.get_tools_schema()
                if tools_schema:
                    request_data["tools"] = tools_schema
                
            await self.event_bus.publish(request_data)
            
            # Сохраняем request_id чтобы потом перехватить ответ
            self.last_request_id = req_id

        elif event.get("type") == "ui_chat_command" and event.get("target") == self.__class__.__name__:
            command = event.get("command")
            if command == "clear_context":
                from datetime import datetime
                # Переименовываем старый файл
                if os.path.exists(self.session_file):
                    dt_str = datetime.now().strftime("%y-%m-%d_%H-%m")
                    new_name = os.path.join(self.history_dir, f"chat_history_{dt_str}.json")
                    os.rename(self.session_file, new_name)
                
                # Создаем новую чистую историю
                self.messages = [
                    {"role": "system", "content": "You are a helpful AI assistant inside the Symcogitant system."}
                ]
                self._save_history()
                
                # Посылаем UI команду на очистку и обновление
                await self.event_bus.publish({
                    "type": "ui_chat_clear",
                    "source": self.__class__.__name__
                })
                await self._send_history_to_ui()
                
            elif command == "sync_context":
                # UI прислал отредактированный контекст
                new_messages = event.get("messages", [])
                if isinstance(new_messages, list):
                    self.messages = new_messages
                    self._save_history()
                    
                    await self.event_bus.publish({
                        "type": "ui_chat_clear",
                        "source": self.__class__.__name__
                    })
                    await self._send_history_to_ui()

        elif event.get("type") == "llm_response" and event.get("request_id") == getattr(self, "last_request_id", None):
            # Получен ответ от LLM
            reply = event.get("reply", "")
            tool_calls = event.get("tool_calls", [])
            ui_reply = reply
            if tool_calls:
                ui_reply += "\n\n🛠 **Вызовы инструментов (Tool Calls):**\n```json\n" + json.dumps(tool_calls, indent=2, ensure_ascii=False) + "\n```"
            
            # Save params for recursive calls
            self.last_llm_model = event.get("model")
            
            usage = event.get("usage", {})
            latency = event.get("latency_sec", 0)
            model_name = event.get("model", "unknown")
            timestamp = event.get("timestamp", "")
            
            # Сохраняем ответ ассистента в историю (для API важно передавать чистые tool_calls)
            assistant_msg = {"role": "assistant", "content": reply}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            
            self.messages.append(assistant_msg)
            self._save_history()
            
            # Отправляем ответ в UI
            await self.event_bus.publish({
                "type": "ui_chat_output",
                "source": self.__class__.__name__,
                "role": "assistant",
                "content": ui_reply,
                "metadata": {
                    "model": model_name,
                    "timestamp": timestamp,
                    "latency_sec": latency,
                    "usage": usage
                }
            })
            
            # Отправляем статистику отдельным системным сообщением
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)
            total_tokens = usage.get('total_tokens', prompt_tokens + completion_tokens)
            
            total_chars = sum(len(m.get("content", "")) for m in self.messages)
            
            stats_msg = f"Ответ получен за {latency:.2f}с. Токены (ЛЛМ): {prompt_tokens} prompt + {completion_tokens} completion = {total_tokens}. Размер контекста (симв): {total_chars}."
            await self.event_bus.publish({
                "type": "ui_chat_system",
                "source": self.__class__.__name__,
                "message": stats_msg
            })

            if tool_calls:
                max_iters = self.config.get("max_tool_iterations", 3)
                if getattr(self, "tool_iteration_count", 0) >= max_iters:
                    await self.event_bus.publish({
                        "type": "ui_chat_system",
                        "source": self.__class__.__name__,
                        "message": f"⚠️ Достигнут лимит вызовов инструментов ({max_iters}). Остановка цепочки."
                    })
                else:
                    # Запрашиваем выполнение инструментов
                    req_id = f"tool_{int(time.time()*1000)}"
                    self.last_tool_request_id = req_id
                    await self.event_bus.publish({
                        "type": "tool_execution_request",
                        "request_id": req_id,
                        "tool_calls": tool_calls
                    })

        elif event.get("type") == "tool_execution_response" and event.get("request_id") == getattr(self, "last_tool_request_id", None):
            if event.get("error"):
                await self.event_bus.publish({
                    "type": "ui_chat_system",
                    "source": self.__class__.__name__,
                    "message": f"⚠️ Ошибка при выполнении инструментов: {event['error']}"
                })
                return
                
            results = event.get("results", [])
            for res in results:
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": res.get("tool_call_id"),
                    "name": res.get("name"),
                    "content": res.get("content")
                }
                self.messages.append(tool_msg)
                # Broadcast tool result to UI so it stays in sync
                await self.event_bus.publish({
                    "type": "ui_chat_output",
                    "source": self.__class__.__name__,
                    "role": "tool",
                    "content": f"**Tool `{tool_msg['name']}` result:**\n```json\n{tool_msg['content']}\n```"
                })
            self._save_history()
            
            self.tool_iteration_count = getattr(self, "tool_iteration_count", 0) + 1
            
            # Отправляем новый запрос в LLM с добавленными результатами
            await self.event_bus.publish({
                "type": "ui_chat_system",
                "source": self.__class__.__name__,
                "message": "Результаты инструментов получены. Возврат в LLM..."
            })
            
            req_id = f"chat_{int(time.time()*1000)}"
            request_data = {
                "type": "llm_request",
                "request_id": req_id,
                "messages": self.messages.copy()
            }
            if getattr(self, "last_llm_model", None):
                request_data["model"] = self.last_llm_model
            if getattr(self, "last_api_key_name", None):
                request_data["api_key_name"] = self.last_api_key_name
            if getattr(self, "last_num_ctx", None):
                request_data["num_ctx"] = self.last_num_ctx

            if hasattr(self.core, "tools_registry") and self.core.tools_registry:
                tools_schema = self.core.tools_registry.get_tools_schema()
                if tools_schema:
                    request_data["tools"] = tools_schema
                    
            await self.event_bus.publish(request_data)
            self.last_request_id = req_id

        elif event.get("type") == "llm_response_error" and event.get("request_id") == getattr(self, "last_request_id", None):
            error_msg = event.get("error", "Unknown error")
            await self.event_bus.publish({
                "type": "ui_chat_system",
                "source": self.__class__.__name__,
                "message": f"Ошибка LLM: {error_msg}"
            })
