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
        self.description = "Базовая проверка безопасности вызовов инструментов"
        self.blocked_tools = {"delete_file", "remove_file", "rm", "rmdir", "delete_directory", "delete"}

    async def on_start(self):
        await self.emit_log("SecurityInterceptor started.")

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
