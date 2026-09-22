import asyncio
import json
import time
from typing import Any

from src.core.base import BaseWorker


class ToolWorker(BaseWorker):
    """
    Воркер, отвечающий за безопасное исполнение инструментов (Tools).
    Принимает запросы на вызов, пропускает через пайплайн интерцепторов в ToolsRegistry
    и возвращает результаты.
    """
    def __init__(self, config: dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Безопасное исполнение инструментов LLM"

    async def on_start(self):
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log("ToolWorker started.")

    async def _handle_event(self, event: dict):
        if event.get("type") == "tool_execution_request":
            asyncio.create_task(self.process_tool_request(event))

    async def process_tool_request(self, event: dict):
        request_id = event.get("request_id", f"tool_req_{int(time.time()*1000)}")
        tool_calls = event.get("tool_calls", [])
        
        if not tool_calls:
            await self.emit_log("Received tool_execution_request with no tool_calls", level="WARNING")
            return
            
        if not hasattr(self.core, "tools_registry") or not self.core.tools_registry:
            error_msg = "ToolsRegistry is not initialized"
            await self.emit_log(error_msg, level="ERROR")
            await self.event_bus.publish({
                "type": "tool_execution_response",
                "request_id": request_id,
                "error": error_msg
            })
            return
            
        results = []
        for call in tool_calls:
            tool_id = call.get("id")
            func_data = call.get("function", {})
            name = func_data.get("name")
            args_str = func_data.get("arguments", "{}")
            
            try:
                args_dict = json.loads(args_str) if isinstance(args_str, str) else args_str
                
                # Исполняем инструмент через ядро (внутри работают Interceptors)
                result = await self.core.tools_registry.execute_tool(name, args_dict)
                
                results.append({
                    "tool_call_id": tool_id,
                    "role": "tool",
                    "name": name,
                    "content": str(result)
                })
                await self.emit_log(f"Executed tool '{name}' successfully.")
            except Exception as e:
                error_str = f"Error executing {name}: {str(e)}"
                results.append({
                    "tool_call_id": tool_id,
                    "role": "tool",
                    "name": name,
                    "content": error_str
                })
                await self.emit_log(error_str, level="WARNING")
                
        await self.event_bus.publish({
            "type": "tool_execution_response",
            "request_id": request_id,
            "results": results
        })
