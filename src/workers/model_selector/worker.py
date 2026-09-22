import asyncio
from typing import Any, Dict, Optional
from src.core.base import BaseWorker

class ModelSelectorWorker(BaseWorker):
    """
    Брокер моделей. Определяет, какую LLM должен использовать плагин
    на основе конфигурации маршрутизации и контекста (например, chat_id).
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Маршрутизатор и брокер LLM моделей для плагинов"
        self.routing = self.config.get("routing", {})

    async def on_start(self):
        await self.emit_log("ModelSelectorWorker started.")

    def get_model(self, plugin_name: str, context: dict) -> Optional[str]:
        """
        Возвращает подходящую модель для плагина на основе контекста.
        Пока возвращает строго по конфигу (без карантина).
        """
        plugin_routing = self.routing.get(plugin_name)
        if not plugin_routing:
            return None
            
        default_model = plugin_routing.get("default_model")
        rules = plugin_routing.get("rules", [])
        
        for rule in rules:
            condition = rule.get("condition", {})
            model = rule.get("model")
            
            # Проверяем все условия в правиле
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
                        
            if match and model:
                return model
                
        return default_model

    def report_failure(self, plugin_name: str, model_name: str, error_type: str):
        """
        Вызывается плагином, если модель вернула ошибку.
        Пока просто логирует, карантин будет реализован позже.
        """
        # TODO: Реализовать умный карантин сбойнувших ЛЛМ (см. ROADMAP)
        asyncio.create_task(self.emit_log(
            f"Plugin {plugin_name} reported failure for model {model_name}: {error_type}", 
            level="WARNING"
        ))
