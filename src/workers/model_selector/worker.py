import asyncio
import time
from typing import Any, Dict, Optional
from src.core.base import BaseWorker

class ModelSelectorWorker(BaseWorker):
    """
    Брокер моделей. Определяет, какую LLM должен использовать плагин
    на основе конфигурации маршрутизации и контекста (например, chat_id).
    Поддерживает умный карантин для временно недоступных моделей.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Маршрутизатор и брокер LLM моделей для плагинов"
        self.routing = self.config.get("routing", {})
        self.quarantine_time = self.config.get("quarantine_time_sec", 300)
        self.quarantine: Dict[str, float] = {}  # model_name -> unban_time

    async def on_start(self):
        await self.emit_log("ModelSelectorWorker started.")

    def _is_quarantined(self, model_name: str) -> bool:
        if model_name in self.quarantine:
            if time.time() < self.quarantine[model_name]:
                return True
            else:
                del self.quarantine[model_name]
        return False

    def get_model(self, plugin_name: str, context: dict) -> Optional[Dict[str, str]]:
        plugin_routing = self.routing.get(plugin_name)
        if not plugin_routing:
            return None
            
        default_model = plugin_routing.get("default_model")
        default_api_key = plugin_routing.get("default_api_key_name")
        rules = plugin_routing.get("rules", [])
        
        for rule in rules:
            condition = rule.get("condition", {})
            model = rule.get("model")
            api_key = rule.get("api_key_name")
            
            match = True
            for key, expected_values in condition.items():
                actual_value = context.get(key)
                if isinstance(expected_values, list):
                    if actual_value not in expected_values:
                        match = False
                        break
                else:
                    if actual_value != expected_values:
                        match = False
                        break
                        
            if match and model and not self._is_quarantined(model):
                return {"model": model, "api_key_name": api_key}
                
        if default_model and not self._is_quarantined(default_model):
            return {"model": default_model, "api_key_name": default_api_key}
        return None

    def report_failure(self, plugin_name: str, model_name: str, error_type: str):
        error_str = str(error_type).lower()
        if "429" in error_str or "rate_limit" in error_str or "503" in error_str:
            unban_time = time.time() + self.quarantine_time
            self.quarantine[model_name] = unban_time
            asyncio.create_task(self.emit_log(
                f"Model {model_name} put in quarantine for {self.quarantine_time}s due to error: {error_type}", 
                level="WARNING"
            ))
            asyncio.create_task(self._quarantine_timer(model_name, self.quarantine_time))
        else:
            asyncio.create_task(self.emit_log(
                f"Plugin {plugin_name} reported failure for model {model_name}: {error_type}", 
                level="WARNING"
            ))

    async def _quarantine_timer(self, model_name: str, delay: int):
        await asyncio.sleep(delay)
        if model_name in self.quarantine and time.time() >= self.quarantine[model_name]:
            del self.quarantine[model_name]
        await self.emit_log(f"Model {model_name} released from quarantine.", "INFO")
        await self.event_bus.publish({
            "type": "models_restored",
            "source": self.__class__.__name__
        })
