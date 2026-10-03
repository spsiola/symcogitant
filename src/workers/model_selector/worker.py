import asyncio
import json
import os
import time
from typing import Any, Dict

from src.core.base import BaseWorker

class ModelSelectorWorker(BaseWorker):
    """
    Брокер моделей. Определяет, какую LLM должен использовать плагин
    на основе тира (smart/fast) или жестко заданных настроек (primary/fallback),
    поддерживая KV-cache affinity.
    Поддерживает умный карантин для временно недоступных моделей.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Маршрутизатор и брокер LLM моделей с поддержкой Cache Affinity"
        self.tiers = self.config.get("tiers", {})
        self.quarantine_time = self.config.get("quarantine_time_sec", 300)
        self.quarantine: Dict[str, float] = {}  # model_name -> unban_time
        
        # talk_id -> {"model": str, "timestamp": float}
        self.cache_affinity: Dict[str, Dict[str, Any]] = {} 
        
        # request_id -> original llm_route_request
        self.pending_requests: Dict[str, dict] = {} 

    async def on_start(self):
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log("ModelSelectorWorker started with Cache Affinity and Tier Routing.", "INFO")

    def _is_quarantined(self, model_name: str) -> bool:
        if model_name in self.quarantine:
            if time.time() < self.quarantine[model_name]:
                return True
            else:
                del self.quarantine[model_name]
        return False

    def _get_registry(self) -> dict:
        registry_path = os.path.join(os.getcwd(), "data", "llm_registry.json")
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _is_model_healthy(self, model_name: str, registry: dict) -> bool:
        if self._is_quarantined(model_name):
            return False
            
        if not registry:
            return True # Fallback if registry broken
            
        models = registry.get("models", {})
        actual_model = model_name.split("#")[0]
        if "/" in actual_model:
            if actual_model not in models:
                pass
        return True

    def _select_model(self, event: dict) -> str:
        registry = self._get_registry()
        
        primary = event.get("primary_model")
        if primary and self._is_model_healthy(primary, registry):
            return primary
            
        fallback = event.get("fallback_model")
        if fallback and self._is_model_healthy(fallback, registry):
            return fallback
            
        if primary or fallback:
            # If explicitly requested but both failed health checks
            return "" 
            
        talk_id = event.get("talk_id")
        tier = event.get("tier", "smart")
        
        # Check cache affinity
        if talk_id in self.cache_affinity:
            affinity = self.cache_affinity[talk_id]
            if time.time() - affinity["timestamp"] < 300: # 5 mins TTL
                last_model = affinity["model"]
                if self._is_model_healthy(last_model, registry) and last_model in self.tiers.get(tier, []):
                    return last_model
        
        # Fallback to tier list
        tier_models = self.tiers.get(tier, [])
        for m in tier_models:
            if self._is_model_healthy(m, registry):
                return m
                
        # Super fallback
        return "qwen2.5-coder:7b"

    async def _handle_event(self, event: dict):
        event_type = event.get("type")
        
        if event_type == "llm_route_request":
            request_id = event.get("request_id")
            model_full = self._select_model(event)
            
            if not model_full:
                await self.emit_log(f"No healthy models available for request {request_id}. Strict mode failed.", "ERROR")
                talk_id = event.get("talk_id")
                await self.event_bus.publish({
                    "type": "talk_send_message",
                    "protocol": talk_id.split(":")[0] if talk_id else "unknown",
                    "talk_id": talk_id,
                    "text": "[СИСТЕМА] Извините, запрашиваемые модели (primary/fallback) сейчас недоступны.",
                    "reply_to_msg_id": None
                })
                return
                
            self.pending_requests[request_id] = {
                "orig_req": event,
                "selected_model": model_full
            }
            
            api_key_name = None
            if "#" in model_full:
                model_base, api_key_name = model_full.split("#", 1)
            else:
                model_base = model_full
            
            llm_req = {
                "type": "llm_request",
                "request_id": request_id,
                "model": model_base,
                "messages": event.get("messages", []),
                "tools": event.get("tools"),
            }
            if api_key_name:
                llm_req["api_key_name"] = api_key_name
                
            await self.emit_log(f"Routing request {request_id} to model {model_full}", "INFO")
            await self.event_bus.publish(llm_req)
            
        elif event_type == "llm_response":
            request_id = event.get("request_id")
            if request_id in self.pending_requests:
                req_data = self.pending_requests.pop(request_id)
                orig_req = req_data["orig_req"]
                selected_model = req_data["selected_model"]
                talk_id = orig_req.get("talk_id")
                
                if talk_id and selected_model:
                    self.cache_affinity[talk_id] = {
                        "model": selected_model,
                        "timestamp": time.time()
                    }
                    
        elif event_type == "llm_response_error":
            request_id = event.get("request_id")
            error_str = str(event.get("error", "")).lower()
            
            if request_id in self.pending_requests:
                req_data = self.pending_requests.pop(request_id)
                orig_req = req_data["orig_req"]
                failed_model = req_data["selected_model"]
                talk_id = orig_req.get("talk_id")
                
                if "429" in error_str or "503" in error_str or "rate limit" in error_str or "overloaded" in error_str:
                    unban_time = time.time() + self.quarantine_time
                    self.quarantine[failed_model] = unban_time
                    await self.emit_log(f"Model {failed_model} quarantined for {self.quarantine_time}s due to error.", "WARNING")
                    asyncio.create_task(self._quarantine_timer(failed_model, self.quarantine_time))
                    
                    # Wipe cache affinity if it was the reason it failed
                    if talk_id in self.cache_affinity and self.cache_affinity[talk_id]["model"] == failed_model:
                        del self.cache_affinity[talk_id]
                        
                    await self.emit_log(f"Retrying request {request_id} with another model...", "INFO")
                    await self.event_bus.publish(orig_req)

    async def _quarantine_timer(self, model_name: str, delay: int):
        await asyncio.sleep(delay)
        if model_name in self.quarantine and time.time() >= self.quarantine[model_name]:
            del self.quarantine[model_name]
        await self.emit_log(f"Model {model_name} released from quarantine.", "INFO")
