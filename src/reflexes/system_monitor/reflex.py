import asyncio
import psutil
import httpx
import time
import os
from dotenv import dotenv_values
from src.core.plugin import BasePlugin

class SystemMonitorReflex(BasePlugin):
    def __init__(self, config, event_bus, core=None):
        super().__init__(config, event_bus, core=core)
        self.interval = self.config.get("interval", 60)
        self.endpoints = self.config.get("llm_endpoints", {})
        self.openrouter_popular_models = self.config.get("openrouter_popular_models", [])
        
        # Load API keys from data/.env
        env_path = os.path.join(os.getcwd(), "data", ".env")
        self.api_keys = dotenv_values(env_path) if os.path.exists(env_path) else {}
        self.last_metric_data = None
        
        # Preload models to serve immediately
        self.all_models = {
            "local": [],
            "cloud": list(self.openrouter_popular_models)
        }
        
    def get_models(self) -> dict:
        """Returns the current list of available models."""
        return self.all_models

    async def check_local_endpoint(self, client: httpx.AsyncClient, name: str, url: str) -> dict:
        try:
            response = await client.get(url, timeout=2.0)
            if response.status_code == 200:
                models = []
                try:
                    data = response.json()
                    if isinstance(data, dict) and "data" in data:
                        models = [m.get("id") for m in data["data"] if m.get("id")]
                    elif isinstance(data, dict) and "models" in data: # Ollama raw API format fallback
                        models = [m.get("name") for m in data["models"] if m.get("name")]
                except Exception:
                    pass
                return {"name": name, "status": "online", "models": models}
            return {"name": name, "status": "error", "models": []}
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
            return {"name": name, "status": "offline", "models": []}

    async def check_cloud_provider(self, client: httpx.AsyncClient, provider: str, url: str, headers: dict, hardcoded_models: list = None) -> dict:
        try:
            # We just test the endpoint to see if auth is valid. 
            # For OpenRouter we can hit /v1/models. For OpenAI /v1/models. 
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                models = hardcoded_models
                if not models:
                    try:
                        data = response.json()
                        if isinstance(data, dict) and "data" in data:
                            models = [f"{provider}/{m.get('id')}" for m in data["data"] if m.get("id")]
                    except Exception:
                        models = []
                return {"name": provider, "status": "online", "models": models}
            return {"name": provider, "status": f"error_{response.status_code}", "models": []}
        except Exception:
            return {"name": provider, "status": "offline", "models": []}

    async def check_anthropic(self, client: httpx.AsyncClient, api_key: str) -> dict:
        # Anthropic doesn't have a simple /models GET endpoint that is standard. 
        # We can just return online if key exists, assuming it works, or send a dummy request.
        # Since we just want to know if they configured it, let's just assume online and hardcode popular.
        models = ["anthropic/claude-3-5-sonnet-20240620", "anthropic/claude-3-opus-20240229", "anthropic/claude-3-haiku-20240307"]
        return {"name": "anthropic", "status": "online", "models": models}

    async def check_google(self, client: httpx.AsyncClient, api_key: str) -> dict:
        models = ["google/gemini-1.5-pro", "google/gemini-1.5-flash", "google/gemini-1.0-pro"]
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        try:
            response = await client.get(url, timeout=10.0)
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "models" in data:
                        models = [m.get("name").replace("models/", "google/") for m in data["models"] if m.get("name")]
                except Exception:
                    pass
                return {"name": "google", "status": "online", "models": models}
            return {"name": "google", "status": f"error_{response.status_code}", "models": []}
        except Exception:
            return {"name": "google", "status": "offline", "models": []}

    async def _handle_event(self, event: dict):
        if event.get("type") == "ui_client_connected":
            if self.last_metric_data:
                await self.event_bus.publish(self.last_metric_data)

    async def run(self):
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log(f"SystemMonitorReflex started, polling every {self.interval}s", "INFO")
        
        async with httpx.AsyncClient() as client:
            while self.running:
                # 1. System Metrics
                cpu_percent = psutil.cpu_percent(interval=None)
                cpu_count = psutil.cpu_count(logical=True)
                
                mem = psutil.virtual_memory()
                mem_total_gb = mem.total / (1024**3)
                mem_used_gb = mem.used / (1024**3)
                mem_percent = mem.percent

                # 2. Extract API keys
                parsed_keys = {}
                for k, v in self.api_keys.items():
                    if "_API_KEY" in k:
                        parts = k.split("_API_KEY", 1)
                        provider = parts[0].lower()
                        key_name = parts[1].strip("_") if len(parts) > 1 and parts[1].strip("_") else "default"
                        if provider not in parsed_keys:
                            parsed_keys[provider] = []
                        parsed_keys[provider].append(key_name)

                # 3. LLM Endpoints
                tasks = []
                # Local endpoints
                for name, url in self.endpoints.items():
                    tasks.append(self.check_local_endpoint(client, name, url))
                
                # Cloud endpoints
                if self.api_keys.get("OPENAI_API_KEY"):
                    tasks.append(self.check_cloud_provider(client, "openai", "https://api.openai.com/v1/models", {"Authorization": f"Bearer {self.api_keys['OPENAI_API_KEY']}"}))
                
                if self.api_keys.get("OPENROUTER_API_KEY"):
                    tasks.append(self.check_cloud_provider(client, "openrouter", "https://openrouter.ai/api/v1/models", {"Authorization": f"Bearer {self.api_keys['OPENROUTER_API_KEY']}"}, self.openrouter_popular_models))
                
                if self.api_keys.get("ANTHROPIC_API_KEY"):
                    tasks.append(self.check_anthropic(client, self.api_keys["ANTHROPIC_API_KEY"]))
                    
                google_key = self.api_keys.get("GOOGLE_API_KEY") or self.api_keys.get("GEMINI_API_KEY")
                if google_key:
                    tasks.append(self.check_google(client, google_key))
                    
                atria_keys = [v for k, v in self.api_keys.items() if k.startswith("ATRIA_API_KEY")]
                if atria_keys:
                    tasks.append(self.check_cloud_provider(client, "atria", "https://api.atria-asi.ai/v1/models", {"Authorization": f"Bearer {atria_keys[0]}"}, ["atria/Atria-Dawn-Preview"]))

                results = await asyncio.gather(*tasks) if tasks else []
                
                llms_dict = {}
                all_models = []
                
                # Separate local and cloud for sorting
                local_models = []
                cloud_models = []

                for res in results:
                    name = res["name"]
                    llms_dict[name] = res["status"]
                    if res["status"] == "online":
                        for m in res["models"]:
                            if name in self.endpoints:
                                local_models.append(m)
                            else:
                                cloud_models.append(m)
                
                # Keep them as a dict to distinguish in UI easily
                self.all_models = {
                    "local": local_models,
                    "cloud": cloud_models
                }

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
                        "llms": llms_dict,
                        "llm_models": self.all_models,
                        "llm_keys": parsed_keys
                    }
                }

                self.last_metric_data = metric_data

                # Publish to EventBus
                await self.event_bus.publish(metric_data)

                # Wait for next interval
                await asyncio.sleep(self.interval)
