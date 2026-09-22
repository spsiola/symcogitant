from typing import Any, Dict, Tuple
from src.core.base import BaseInterceptor

class SecurityInterceptor(BaseInterceptor):
    """
    Интерцептор безопасности. Проверяет аргументы перед вызовом инструмента.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Базовая проверка безопасности вызовов инструментов"

    async def on_start(self):
        await self.emit_log("SecurityInterceptor started.")

    async def pre_tool_call(self, name: str, kwargs: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Вызывается из ToolsRegistry перед выполнением инструмента.
        Должен вернуть (True, "") если вызов разрешен, или (False, "причина") если запрещен.
        """
        await self.emit_log(f"[SECURITY] Checking tool call '{name}' with args: {kwargs}")
        
        # На первом этапе - заглушка, разрешаем всё.
        # В будущем здесь можно проверять kwargs['path'] на выход за пределы песочницы и т.д.
        return True, ""
