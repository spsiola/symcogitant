import json
import os
import asyncio
from typing import Any, Dict
from src.core.base import BaseWorker

class ContextAssemblerWorker(BaseWorker):
    """
    Умный сборщик контекста (Context Assembler).
    Принимает запросы `context_assembly_request`, читает файлы, обрезает историю
    и формирует итоговый промпт на основе декларативной схемы.
    Затем отправляет его в ModelSelectorWorker через `llm_route_request`.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Умный сборщик контекста по декларативным схемам"
        
    async def on_start(self):
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log("ContextAssemblerWorker started.", "INFO")
        
    async def _handle_event(self, event: dict):
        if event.get("type") == "context_assembly_request":
            await self._process_request(event)

    async def _process_request(self, event: dict):
        req_id = event.get("request_id")
        talk_id = event.get("talk_id")
        raw_messages = event.get("messages", [])
        schema = event.get("schema", {})
        variables = event.get("variables", {})
        routing = event.get("routing", {})
        tools_capabilities = event.get("tools_capabilities", {})
        
        try:
            # 1. System Blocks
            system_prompt = await self._build_system_blocks(schema.get("system_blocks", []), variables)
            
            # 2. History Blocks
            history_blocks_schema = schema.get("history_blocks", {})
            formatted_messages = self._build_history(raw_messages, history_blocks_schema)
            
            # 3. Postfix Blocks
            postfix = await self._build_postfix_blocks(schema.get("postfix_blocks", []), variables)
            
            # Внедрение постфикса
            if postfix:
                if not formatted_messages:
                    formatted_messages.append({"role": "user", "content": postfix})
                else:
                    last_msg = formatted_messages[-1]
                    if last_msg["role"] == "user":
                        last_msg["content"] += f"\n\n{postfix}"
                    else:
                        formatted_messages.append({"role": "user", "content": postfix})

            # Сборка финального массива messages для LLM
            llm_messages = [{"role": "system", "content": system_prompt}] + formatted_messages
            
            # 4. Сборка инструментов
            tools_list = self._build_tools(schema.get("tools", {}), tools_capabilities)
            
            # Отправка дальше по пайплайну (DLP проверка произойдет автоматически в шине EventBus)
            request_data = {
                "type": "llm_route_request",
                "request_id": req_id,
                "talk_id": talk_id,
                "messages": llm_messages,
                "tools": tools_list,
                "tier": routing.get("tier", "smart"),
                "primary_model": routing.get("primary_model", ""),
                "fallback_model": routing.get("fallback_model", "")
            }
            await self.emit_log(f"Context assembled for {talk_id}, passing to router.", "DEBUG")
            await self.event_bus.publish(request_data)
            
        except Exception as e:
            await self.emit_log(f"Error assembling context for {talk_id}: {e}", "ERROR")
            await self.event_bus.publish({
                "type": "llm_response_error",
                "request_id": req_id,
                "error": f"Context Assembly failed: {e}"
            })

    async def _build_system_blocks(self, blocks: list, variables: dict) -> str:
        parts = []
        for block in blocks:
            b_type = block.get("type")
            if b_type == "file":
                path = block.get("path", "")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        parts.append(f.read().strip())
                else:
                    await self.emit_log(f"System block file not found: {path}", "WARNING")
            elif b_type == "json_field":
                path = block.get("path", "")
                field = block.get("json_path", "")
                if os.path.exists(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        val = data
                        for k in field.split("."):
                            if isinstance(val, dict):
                                val = val.get(k, "")
                        if val:
                            parts.append(str(val))
                    except Exception as e:
                        await self.emit_log(f"Error reading json field {field} from {path}: {e}", "WARNING")
            elif b_type == "dynamic":
                template = block.get("template", "")
                parts.append(template.format(**variables))
            elif b_type == "text":
                parts.append(block.get("text", ""))
                
        return "\n\n".join(parts)

    def _build_history(self, raw_messages: list, schema: dict) -> list:
        # TODO: Добавить умную обрезку (max_tokens / max_chars / FULL_TEXT_TAIL)
        # Пока просто берем хвост и форматируем как скрипт
        max_msgs = schema.get("max_messages", 100)
        messages_to_process = raw_messages[-max_msgs:] if raw_messages else []
        
        script_parts = []
        for msg in messages_to_process:
            sender = msg.get("sender", {})
            sender_name = sender.get("name", "Unknown")
            is_me = sender.get("is_me", False)
            role_val = sender.get("role", "user")
            
            role_label = "Assistant (Me)" if is_me else f"{str(role_val).capitalize()} ({sender_name})"
            
            status_flags = []
            if msg.get("is_deleted"):
                status_flags.append("[DELETED]")
            if msg.get("is_edited"):
                status_flags.append(f"[EDITED at {msg.get('edited_date')}]")
            
            flags_str = " ".join(status_flags)
            if flags_str:
                flags_str = " " + flags_str
                
            time_str = f"[{msg.get('date')}] " if msg.get("date") else ""
            
            reactions_str = ""
            reactions = msg.get("reactions", [])
            if reactions:
                reacts = ", ".join(f"{r.get('user_id')}: {r.get('emoji')}" for r in reactions)
                reactions_str = f"\n[Reactions: {reacts}]"
            
            text = msg.get("text", "")
            script_parts.append(f"{time_str}{role_label}{flags_str}:\n{text}{reactions_str}")

        if not script_parts:
            return []
            
        full_context = "\n\n".join(script_parts)
        return [{"role": "user", "content": full_context}]

    async def _build_postfix_blocks(self, blocks: list, variables: dict) -> str:
        # Постфикс собирается так же, как и системный блок
        return await self._build_system_blocks(blocks, variables)

    def _build_tools(self, tools_schema: dict, tools_capabilities: dict) -> list:
        tools_list = []
        
        # Базовые инструменты из возможностей транспорта (capabilities)
        if tools_capabilities.get("can_send_text", False):
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
            
        # Дополнительные инструменты
        allowed = tools_schema.get("allowed", [])
        if allowed and self.core and hasattr(self.core, "tools_registry"):
            all_schemas = self.core.tools_registry.get_tools_schema()
            if all_schemas:
                for tool_name in allowed:
                    for schema in all_schemas:
                        if schema.get("type") == "function" and schema.get("function", {}).get("name") == tool_name:
                            tools_list.append(schema)
                            
        return tools_list
