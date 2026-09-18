import asyncio
import psutil
import httpx
import time
from src.core.plugin import BasePlugin

class SystemMonitorReflex(BasePlugin):
    def __init__(self, config, event_bus):
        super().__init__(config, event_bus)
        self.interval = self.config.get("interval", 60)
        self.endpoints = self.config.get("llm_endpoints", {})

    async def check_endpoint(self, client: httpx.AsyncClient, name: str, url: str) -> dict:
        try:
            # Short timeout so we don't hang if the server is off
            response = await client.get(url, timeout=2.0)
            return {"name": name, "status": "online" if response.status_code == 200 else "error"}
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
            return {"name": name, "status": "offline"}

    async def run(self):
        await self.emit_log(f"SystemMonitorReflex started, polling every {self.interval}s", "INFO")
        
        # httpx client for reuse
        async with httpx.AsyncClient() as client:
            while self.running:
                # 1. System Metrics
                cpu_percent = psutil.cpu_percent(interval=None)
                cpu_count = psutil.cpu_count(logical=True)
                
                mem = psutil.virtual_memory()
                mem_total_gb = mem.total / (1024**3)
                mem_used_gb = mem.used / (1024**3)
                mem_percent = mem.percent

                # 2. LLM Endpoints
                tasks = [self.check_endpoint(client, name, url) for name, url in self.endpoints.items()]
                llm_statuses = await asyncio.gather(*tasks) if tasks else []
                
                llms_dict = {item['name']: item['status'] for item in llm_statuses}

                # Construct metric payload
                metric_data = {
                    "type": "metric",
                    "source": "SystemMonitorReflex",
                    "timestamp": time.time(),
                    "data": {
                        "cpu": {
                            "cores": cpu_count,
                            "usage_percent": cpu_percent
                        },
                        "ram": {
                            "total_gb": round(mem_total_gb, 2),
                            "used_gb": round(mem_used_gb, 2),
                            "usage_percent": mem_percent
                        },
                        "llms": llms_dict
                    }
                }

                # Publish to EventBus
                await self.event_bus.publish(metric_data)

                # Wait for next interval
                await asyncio.sleep(self.interval)
