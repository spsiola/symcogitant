import asyncio
import json
import os
import shutil
import time
from datetime import UTC, datetime

import httpx
import psutil
from dotenv import dotenv_values

from src.core.plugin import BasePlugin


class SystemMonitorReflex(BasePlugin):
    def __init__(self, config, event_bus, core=None):
        super().__init__(config, event_bus, core=core)
        self.description = "Сбор системных метрик и мониторинг доступности локальных нейросетей."
        self.interval = self.config.get("interval", 60)
        self.endpoints = self.config.get("llm_endpoints", {})
        
        env_path = os.path.join(os.getcwd(), "data", ".env")
        self.api_keys = dotenv_values(env_path) if os.path.exists(env_path) else {}
        self.last_metric_data = None
        
        self.registry_path = os.path.join(os.getcwd(), "data", "llm_registry.json")
        self.llm_registry = self._load_registry()

        self.all_models = {
            "local": [],
            "cloud": []
        }
        
    def get_models(self) -> dict:
        return self.all_models
        
    def _load_registry(self) -> dict:
        if os.path.exists(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading registry: {e}")
        return {"models": {}, "accounts": {}}

    def _save_registry(self):
        try:
            if os.path.exists(self.registry_path):
                ts = datetime.now(UTC).strftime("%y-%m-%d_%H-%M")
                backup_path = self.registry_path.replace(".json", f"_{ts}.json")
                shutil.copy2(self.registry_path, backup_path)
            
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(self.llm_registry, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving registry: {e}")

    async def check_local_endpoint(self, client: httpx.AsyncClient, name: str, url: str) -> dict:
        try:
            response = await client.get(url, timeout=2.0)
            if response.status_code == 200:
                models = []
                try:
                    data = response.json()
                    if isinstance(data, dict) and "data" in data:
                        models = [m.get("id") for m in data["data"] if m.get("id")]
                    elif isinstance(data, dict) and "models" in data:
                        models = [m.get("name") for m in data["models"] if m.get("name")]
                except Exception:
                    pass
                return {"name": name, "status": "online", "models": models}
            return {"name": name, "status": "error", "models": []}
        except Exception:
            return {"name": name, "status": "offline", "models": []}

    async def _handle_event(self, event: dict):
        if event.get("type") == "ui_client_connected":
            if self.last_metric_data:
                await self.event_bus.publish(self.last_metric_data)

    async def run(self):
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log(f"SystemMonitorReflex started, metrics every {self.interval}s", "INFO")
        
        self.running = True
        task_fast = asyncio.create_task(self._loop_fast())
        task_med = asyncio.create_task(self._loop_medium())
        task_slow = asyncio.create_task(self._loop_slow())
        
        while self.running:
            await asyncio.sleep(1)
            
        task_fast.cancel()
        task_med.cancel()
        task_slow.cancel()

    async def _loop_fast(self):
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    cpu_percent = psutil.cpu_percent(interval=None)
                    cpu_count = psutil.cpu_count(logical=True)
                    
                    mem = psutil.virtual_memory()
                    mem_total_gb = mem.total / (1024**3)
                    mem_used_gb = mem.used / (1024**3)
                    
                    parsed_keys = {}
                    for k, v in self.api_keys.items():
                        if "_API_KEY" in k:
                            parts = k.split("_API_KEY", 1)
                            provider = parts[0].lower()
                            key_name = parts[1].strip("_") if len(parts) > 1 and parts[1].strip("_") else "default"
                            if provider not in parsed_keys:
                                parsed_keys[provider] = []
                            parsed_keys[provider].append(key_name)

                    tasks = []
                    for name, url in self.endpoints.items():
                        tasks.append(self.check_local_endpoint(client, name, url))
                    
                    results = await asyncio.gather(*tasks) if tasks else []
                    llms_dict = {}
                    local_models = []
                    
                    for res in results:
                        name = res["name"]
                        llms_dict[name] = res["status"]
                        if res["status"] == "online":
                            for m in res["models"]:
                                if name in self.endpoints:
                                    local_models.append(m)
                                    
                    for provider in parsed_keys.keys():
                        if provider not in llms_dict:
                            status = "online"
                            # Check registry for actual status
                            provider_keys = self.llm_registry.get("accounts", {}).get(provider, {}).get("keys", {})
                            for k_info in provider_keys.values():
                                if k_info.get("status") == "error":
                                    status = "error"
                                    break
                            llms_dict[provider] = status
                                    
                    cloud_models = list(self.llm_registry.get("models", {}).keys())
                    self.all_models = {"local": local_models, "cloud": cloud_models}

                    metric_data = {
                        "type": "metric",
                        "source": "SystemMonitorReflex",
                        "timestamp": time.time(),
                        "data": {
                            "cpu": {"cores": cpu_count, "usage_percent": cpu_percent},
                            "ram": {"total_gb": round(mem_total_gb, 2), "used_gb": round(mem_used_gb, 2), "usage_percent": mem.percent},
                            "llms": llms_dict,
                            "llm_models": self.all_models,
                            "llm_keys": parsed_keys,
                            "llm_registry": self.llm_registry
                        }
                    }
                    self.last_metric_data = metric_data
                    await self.event_bus.publish(metric_data)
                except Exception as e:
                    await self.emit_log(f"[Fast Loop] Error: {e}", "ERROR")
                await asyncio.sleep(self.interval)

    async def _update_balances(self, client: httpx.AsyncClient):
        await self.emit_log("[Medium Loop] Updating billing & limits...", "INFO")
        # Update accounts and budgets in registry
        accounts = self.llm_registry.setdefault("accounts", {})
        dirty = False
        now_iso = datetime.now(UTC).isoformat()
        
        for k, v in self.api_keys.items():
            if "_API_KEY" in k:
                parts = k.split("_API_KEY", 1)
                provider = parts[0].lower()
                key_name = parts[1].strip("_") if len(parts) > 1 and parts[1].strip("_") else "default"
                
                provider_data = accounts.setdefault(provider, {"keys": {}, "budgets": {}})
                key_data = provider_data["keys"].setdefault(key_name, {})
                
                # Assign a default shared budget ID based on provider if not present
                budget_id = key_data.get("shared_budget_id", f"{provider}_main")
                key_data["shared_budget_id"] = budget_id
                
                budget_data = provider_data["budgets"].setdefault(budget_id, {"balance": 0.0})
                
                # OpenRouter logic
                if provider == "openrouter":
                    try:
                        resp = await client.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {v}"}, timeout=10.0)
                        if resp.status_code == 200:
                            data = resp.json().get("data", {})
                            usage = float(data.get("usage", 0))
                            limit = data.get("limit")
                            key_data["status"] = "online"
                            if limit is not None:
                                key_data["limit"] = float(limit)
                                budget_data["balance"] = float(limit) - usage
                            else:
                                budget_data["balance"] = -usage # Just track usage as negative if no limit
                            
                            budget_data["last_updated"] = now_iso
                            dirty = True
                    except Exception:
                        key_data["status"] = "error"
        
        if dirty:
            self._save_registry()

    async def _loop_medium(self):
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    await self._update_balances(client)
                except Exception as e:
                    await self.emit_log(f"[Medium Loop] Error: {e}", "ERROR")
                await asyncio.sleep(3600)

    async def _update_models_registry(self, client: httpx.AsyncClient):
        await self.emit_log("[Slow Loop] Updating model registry...", "INFO")
        models_reg = self.llm_registry.setdefault("models", {})
        dirty = False
        now_iso = datetime.now(UTC).isoformat()
        

        # OpenRouter Models
        try:
            resp = await client.get("https://openrouter.ai/api/v1/models", timeout=20.0)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                popular_models = self.config.get("openrouter_popular_models")
                
                # Cleanup existing openrouter models not in popular list
                if popular_models is not None:
                    keys_to_delete = []
                    for mid, mdata in models_reg.items():
                        if mdata.get("router_provider") == "openrouter" and mid not in popular_models:
                            keys_to_delete.append(mid)
                    for k in keys_to_delete:
                        del models_reg[k]
                        dirty = True
                
                for m in data:
                    mid = f"openrouter/{m.get('id')}"
                    if popular_models is not None and mid not in popular_models:
                        continue
                        
                    mdata = models_reg.setdefault(mid, {})
                    mdata["name"] = m.get("name")
                    mdata["router_provider"] = "openrouter"
                    mdata["native_provider"] = m.get("id", "").split("/")[0] if "/" in m.get("id", "") else "unknown"
                    mdata["context_length"] = m.get("context_length", 0)
                    
                    pricing = m.get("pricing", {})
                    mdata["pricing"] = {
                        "prompt": float(pricing.get("prompt", 0)),
                        "completion": float(pricing.get("completion", 0))
                    }
                    mdata["last_updated"] = now_iso
                dirty = True
        except Exception:
            pass
            
        # Atria Models (custom)
        atria_keys = [v for k, v in self.api_keys.items() if k.startswith("ATRIA_API_KEY")]
        if atria_keys:
            try:
                resp = await client.get("https://api.atria-asi.ai/v1/models", headers={"Authorization": f"Bearer {atria_keys[0]}"}, timeout=20.0)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for m in data:
                        mid = f"atria/{m.get('id')}"
                        mdata = models_reg.setdefault(mid, {})
                        mdata["name"] = m.get("id")
                        mdata["router_provider"] = "atria"
                        mdata["native_provider"] = "atria"
                        mdata["last_updated"] = now_iso
                    dirty = True
            except Exception:
                pass

        if dirty:
            self._save_registry()

    async def _loop_slow(self):
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    await self._update_models_registry(client)
                except Exception as e:
                    await self.emit_log(f"[Slow Loop] Error: {e}", "ERROR")
                await asyncio.sleep(86400)
