import os
import re
from typing import Any, Dict, Tuple
from src.core.base import BaseInterceptor

class SecurityInterceptor(BaseInterceptor):
    """
    Интерцептор безопасности. Проверяет аргументы перед вызовом инструмента.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Базовая проверка безопасности вызовов инструментов и контроль утечек (DLP)"
        self.blocked_tools = {"delete_file", "remove_file", "rm", "rmdir", "delete_directory", "delete"}
        self.secrets_to_protect = self._load_env_secrets()

    def _load_env_secrets(self) -> list:
        secrets = []
        env_path = os.path.abspath(os.path.join(os.getcwd(), "data", ".env"))
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        _, val = line.split("=", 1)
                        val = val.strip().strip("'\"")
                        if len(val) > 4:  # Игнорируем короткие значения типа "1" или "true"
                            secrets.append(val)
        return secrets

    def mask_llm_payload(self, messages: list) -> bool:
        """
        DLP проверка: ищет совпадения значений из .env в тексте промпта.
        Вместо блокировки заменяет секрет на маску [СКРЫТО by DLP interceptor].
        Возвращает True, если была найдена и замаскирована утечка.
        """
        leaked = False
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                for secret in self.secrets_to_protect:
                    if secret in content:
                        content = content.replace(secret, "[СКРЫТО by DLP interceptor]")
                        leaked = True
                msg["content"] = content
        return leaked

    async def on_start(self):
        self.event_bus.add_interceptor(self.intercept_event)
        await self.emit_log("SecurityInterceptor started (DLP masking active via EventBus).")

    async def intercept_event(self, event: dict) -> bool:
        """Перехватчик событий шины (EventBus Middleware)."""
        if event.get("type") == "llm_route_request":
            messages = event.get("messages", [])
            was_leaked = self.mask_llm_payload(messages)
            if was_leaked:
                await self.emit_log("DLP Interceptor modified the payload to hide confidential data (.env secrets).", "WARNING")
            
            # Мы не блокируем событие, а пропускаем его дальше (с уже замаскированным контентом)
            return True
        return True

    async def pre_tool_call(self, name: str, kwargs: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Вызывается из ToolsRegistry перед выполнением инструмента.
        Должен вернуть (True, "") если вызов разрешен, или (False, "причина") если запрещен.
        """
        await self.emit_log(f"[SECURITY] Checking tool call '{name}' with args: {kwargs}")
        
        if name in self.blocked_tools:
            return False, f"Tool '{name}' is forbidden by security policies (file deletion blocked)."

        if name == "run_command":
            command = kwargs.get("command", "")
            
            # 1. Directory Traversal
            if "../" in command:
                return False, "Security violation: Directory traversal ('../') is not allowed in run_command."
                
            # 2. Block dangerous binaries
            dangerous_binaries = r'\b(rm|sudo|mv|chmod|chown|kill|pkill)\b'
            if re.search(dangerous_binaries, command):
                return False, "Security violation: Dangerous command used (rm, sudo, mv, chmod, chown, kill, pkill)."
                
            # 3. Block redirecting output to absolute root paths
            if "> /" in command or ">> /" in command:
                return False, "Security violation: Writing to absolute root paths is not allowed."

        base_dir = os.path.abspath(os.getcwd())

        for key, val in kwargs.items():
            if isinstance(val, str):
                # Apply path checking only for arguments that seem to represent paths
                key_lower = key.lower()
                if "path" in key_lower or "file" in key_lower or "dir" in key_lower:
                    resolved = os.path.abspath(val)
                    # Check if the resolved path starts with the base directory
                    if not resolved.startswith(base_dir):
                        return False, f"Access denied: path '{val}' in parameter '{key}' is outside the working directory."
        
        return True, ""
