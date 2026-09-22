import inspect
import importlib.util
import os
import sys
import logging
from typing import Any, Callable, Dict, List
from pydantic import create_model

logger = logging.getLogger(__name__)

def tool(func: Callable) -> Callable:
    """Decorator to mark a function as an LLM tool."""
    setattr(func, "__is_llm_tool__", True)
    return func

class ToolsRegistry:
    def __init__(self, core: Any = None):
        self.tools: Dict[str, Callable] = {}
        self.schemas: List[Dict[str, Any]] = []
        self.core = core

    def load_from_directory(self, directory: str):
        if not os.path.isdir(directory):
            logger.debug(f"Directory {directory} not found for tools.")
            return

        # Add to sys.path so modules can import locally if needed
        if directory not in sys.path:
            sys.path.insert(0, directory)

        for root, _, files in os.walk(directory):
            for file in files:
                if file.endswith(".py") and not file.startswith("__"):
                    # For simplicity, module name is filename without .py
                    # In a nested structure, you'd want a proper dotted path, but this is a start.
                    module_name = file[:-3] 
                    filepath = os.path.join(root, file)
                    self._load_module(module_name, filepath)

    def load_skills_from_directory(self, directory: str):
        """Сканирует директорию на наличие файлов SKILL.md и регистрирует их как инструменты."""
        if not os.path.isdir(directory):
            logger.debug(f"Directory {directory} not found for skills.")
            return
            
        for root, _, files in os.walk(directory):
            for file in files:
                if file == "SKILL.md":
                    filepath = os.path.join(root, file)
                    self._load_skill(filepath)

    def _load_skill(self, filepath: str):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Простейший парсинг YAML frontmatter
            name = None
            description = ""
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    for line in frontmatter.split("\n"):
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip()
                            
            if not name:
                logger.warning(f"Skipping skill {filepath}: no name found in frontmatter.")
                return
                
            # Создаем функцию-обертку, которая просто возвращает содержимое файла
            def make_skill_handler(skill_content: str):
                def skill_func() -> str:
                    return f"[SKILL INSTRUCTION]\n{skill_content}"
                return skill_func
                
            func = make_skill_handler(content)
            func.__name__ = name
            func.__doc__ = description
            
            self.register_tool(func)
            logger.info(f"Loaded skill: {name}")
            
        except Exception as e:
            logger.error(f"Error loading skill {filepath}: {e}")

    def _load_module(self, module_name: str, filepath: str):
        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                # Scan for functions with __is_llm_tool__ == True
                for name, obj in inspect.getmembers(module, inspect.isfunction):
                    if getattr(obj, "__is_llm_tool__", False):
                        self.register_tool(obj)
        except Exception as e:
            logger.error(f"Error loading tool module {filepath}: {e}")

    def register_tool(self, func: Callable):
        name = func.__name__
        # Avoid duplicate registration
        if name in self.tools:
            logger.warning(f"Tool {name} is already registered. Overwriting.")
        self.tools[name] = func
        
        # Generate JSON Schema
        sig = inspect.signature(func)
        fields = {}
        for param_name, param in sig.parameters.items():
            annotation = param.annotation if param.annotation != inspect.Parameter.empty else Any
            default = param.default if param.default != inspect.Parameter.empty else ...
            fields[param_name] = (annotation, default)
        
        # Create a pydantic model for schema generation
        model = create_model(f'{name}_args', **fields)
        schema = model.model_json_schema()
        
        # Remove pydantic's root title field to keep schema clean
        schema.pop("title", None)
        
        docstring = inspect.getdoc(func) or ""
        
        tool_schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": docstring,
                "parameters": schema
            }
        }
        
        # Update or append schema
        existing_idx = next((i for i, s in enumerate(self.schemas) if s["function"]["name"] == name), None)
        if existing_idx is not None:
            self.schemas[existing_idx] = tool_schema
        else:
            self.schemas.append(tool_schema)
            
        logger.info(f"Registered tool: {name}")

    def register_external_tool(self, name: str, schema: dict, func: Callable):
        """Register an external tool (e.g., from MCP) directly with its schema and handler."""
        if name in self.tools:
            logger.warning(f"External tool {name} is already registered. Overwriting.")
        
        self.tools[name] = func
        
        tool_schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": schema.get("description", ""),
                "parameters": schema.get("inputSchema", schema.get("parameters", {}))
            }
        }
        
        existing_idx = next((i for i, s in enumerate(self.schemas) if s["function"]["name"] == name), None)
        if existing_idx is not None:
            self.schemas[existing_idx] = tool_schema
        else:
            self.schemas.append(tool_schema)
            
        logger.info(f"Registered external tool: {name}")

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        return self.schemas

    async def execute_tool(self, name: str, kwargs: Dict[str, Any]) -> Any:
        if name not in self.tools:
            raise ValueError(f"Tool {name} not found in registry.")
            
        # Security Interceptor Pipeline
        if self.core and hasattr(self.core, "interceptors"):
            for interceptor in self.core.interceptors:
                if hasattr(interceptor, "pre_tool_call"):
                    allowed, reason = await interceptor.pre_tool_call(name, kwargs)
                    if not allowed:
                        logger.warning(f"Tool {name} blocked by {interceptor.__class__.__name__}: {reason}")
                        raise PermissionError(f"Tool {name} blocked: {reason}")

        func = self.tools[name]
        
        # Выполнение инструмента (пока поддерживаем только синхронные инструменты,
        # но если инструмент асинхронный, его нужно await'ить)
        if inspect.iscoroutinefunction(func):
            return await func(**kwargs)
        else:
            return func(**kwargs)
