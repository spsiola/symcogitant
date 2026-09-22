import asyncio
import logging
from typing import Any, Dict
from contextlib import AsyncExitStack

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

from src.core.base import BaseDaemon

logger = logging.getLogger(__name__)

class MCPManagerDaemon(BaseDaemon):
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Управляет жизненным циклом MCP серверов"
        
        core_config = self.core.load_config() if self.core and hasattr(self.core, "load_config") else {}
        self.mcp_servers = core_config.get("mcp_servers", {})
        self.sessions = {}
        self.exit_stack = AsyncExitStack()

    async def run(self):
        self.running = True
        await self.emit_log("MCPManagerDaemon started.", "INFO")
        
        # Initialize servers
        for server_name, server_config in self.mcp_servers.items():
            await self._start_server(server_name, server_config)
            
        # Keep running until daemon is stopped
        while self.running:
            await asyncio.sleep(1)
            
        # Cleanup on exit
        try:
            await asyncio.wait_for(self.exit_stack.aclose(), timeout=2.0)
        except Exception as e:
            logger.warning(f"MCP exit stack cleanup timeout/error: {e}")

    async def _start_server(self, name: str, config: dict):
        try:
            server_params = StdioServerParameters(
                command=config.get("command"),
                args=config.get("args", []),
                env=None
            )
            
            # Using exit_stack to keep contexts alive as long as daemon runs
            read, write = await self.exit_stack.enter_async_context(stdio_client(server_params))
            session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            
            await session.initialize()
            self.sessions[name] = session
            await self.emit_log(f"MCP Server '{name}' initialized successfully.", "INFO")
            
            # List tools and register them
            tools_response = await session.list_tools()
            for tool in getattr(tools_response, "tools", []):
                self._register_mcp_tool(name, session, tool)
                
        except Exception as e:
            await self.emit_log(f"Failed to start MCP server '{name}': {e}", "ERROR")

    def _register_mcp_tool(self, server_name: str, session: ClientSession, tool_info: Any):
        """Wraps an MCP tool into a local callable and registers it with ToolsRegistry"""
        
        async def mcp_tool_wrapper(**kwargs):
            await self.emit_log(f"Executing MCP tool '{tool_info.name}' on server '{server_name}'", "INFO")
            result = await session.call_tool(tool_info.name, arguments=kwargs)
            
            # Format result to string for the LLM
            formatted_results = []
            if hasattr(result, "content") and result.content:
                for content_item in result.content:
                    if hasattr(content_item, "text"):
                        formatted_results.append(content_item.text)
            return "\n".join(formatted_results) if formatted_results else "No output from MCP tool"

        # The MCP library returns an object with name, description, and inputSchema
        schema = {
            "description": getattr(tool_info, "description", ""),
            "inputSchema": getattr(tool_info, "input_schema", {})
        }
        
        if self.core and hasattr(self.core, "tools_registry"):
            self.core.tools_registry.register_external_tool(
                name=tool_info.name, 
                schema=schema, 
                func=mcp_tool_wrapper
            )
            logger.info(f"Registered MCP tool {tool_info.name} from server {server_name}")
