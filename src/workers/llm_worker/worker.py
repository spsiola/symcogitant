import asyncio
import os
import time
from datetime import UTC
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from google import genai
from openai import AsyncOpenAI

from src.core.base import BaseWorker


class LLMWorker(BaseWorker):
    """
    Рефлекс общения с LLM. Поддерживает OpenAI, Anthropic, Google Gemini, OpenRouter и локальные модели.
    """
    def __init__(self, config: dict[str, Any], event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core=core)
        self.description = "Интеграция с LLM, кэширование и расчет стоимости запросов."
        
        # Получаем параметры подключения
        self.default_base_url = self.config.get("base_url", "http://127.0.0.1:11434/v1")
        self.default_api_key = self.config.get("api_key", "ollama")
        self.default_model = self.config.get("default_model", "qwen2.5-coder:7b")
        
        env_path = os.path.join(os.getcwd(), "data", ".env")
        self.api_keys = dotenv_values(env_path) if os.path.exists(env_path) else {}
        
        # Default local client
        self.local_client = AsyncOpenAI(
            base_url=self.config.get("local_api_base", self.default_base_url),
            api_key=self.config.get("local_api_key", self.default_api_key)
        )
        
        # Cloud clients initialized lazily or proactively
        self.anthropic_client = None
        self.gemini_client = None
        if self.api_keys.get("ANTHROPIC_API_KEY"):
            self.anthropic_client = AsyncAnthropic(api_key=self.api_keys["ANTHROPIC_API_KEY"])
            
        google_api_key = self.api_keys.get("GOOGLE_API_KEY") or self.api_keys.get("GEMINI_API_KEY")
        if google_api_key:
            self.gemini_client = genai.Client(api_key=google_api_key)

    async def on_start(self):
        self.event_bus.subscribe(self._handle_event)
        await self.emit_log(f"LLM Worker started. Default Base URL: {self.default_base_url}")

    def _resolve_api_key(self, provider_prefix: str, api_key_name: str | None = None) -> str:
        """Resolves the API key from .env based on provider and optional name."""
        prefix = f"{provider_prefix.upper()}_API_KEY"
        if api_key_name and api_key_name != "default":
            key = self.api_keys.get(f"{prefix}_{api_key_name}")
            if key:
                return key
        
        # Fallback to default key or the first one available
        key = self.api_keys.get(prefix)
        if key:
            return key
            
        # Try to find any key starting with prefix
        for k, v in self.api_keys.items():
            if k.startswith(prefix):
                return v
        return None

    async def _handle_event(self, event: dict):
        if event.get("type") == "llm_request":
            asyncio.create_task(self.process_llm_request(event))

    async def process_llm_request(self, event: dict):
        request_id = event.get("request_id", f"req_{int(time.time()*1000)}")
        messages = event.get("messages", [])
        model = event.get("model", self.default_model)
        temperature = event.get("temperature", 0.7)
        
        if not messages:
            await self.emit_log("Received llm_request without messages.", level="WARNING")
            return
            
        await self.emit_log(f"Sending request {request_id} to model '{model}' with {len(messages)} messages.")
        
        start_time = time.time()
        try:
            reply_text = ""
            prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
            
            if model.startswith("anthropic/"):
                actual_model = model.replace("anthropic/", "")
                if not self.anthropic_client:
                    raise ValueError("Anthropic API key not configured")
                    
                # Anthropic requires system prompt as a separate parameter
                system_prompt = next((m["content"] for m in messages if m["role"] == "system"), "")
                user_msgs = [{"role": m["role"], "content": str(m.get("content", ""))} for m in messages if m["role"] != "system"]
                
                response = await self.anthropic_client.messages.create(
                    model=actual_model,
                    system=system_prompt,
                    messages=user_msgs,
                    max_tokens=4096,
                    temperature=temperature
                )
                reply_text = response.content[0].text
                prompt_tokens = response.usage.input_tokens
                completion_tokens = response.usage.output_tokens
                total_tokens = prompt_tokens + completion_tokens

            elif model.startswith("google/"):
                actual_model = model.replace("google/", "")
                system_instruction = next((m["content"] for m in messages if m["role"] == "system"), None)
                
                formatted_msgs = []
                for m in messages:
                    if m["role"] == "system":
                        continue
                    role = "user" if m["role"] == "user" else "model"
                    # google-genai uses 'model' instead of 'assistant' usually, but 'user' and 'model' is typical.
                    formatted_msgs.append({"role": role, "parts": [{"text": m["content"]}]})
                
                if not getattr(self, "gemini_client", None):
                    raise ValueError("Google API Key is not configured")
                    
                config_kwargs = {"temperature": temperature}
                if system_instruction:
                    config_kwargs["system_instruction"] = system_instruction
                
                response = await self.gemini_client.aio.models.generate_content(
                    model=actual_model,
                    contents=formatted_msgs,
                    config=genai.types.GenerateContentConfig(**config_kwargs)
                )
                reply_text = response.text
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    prompt_tokens = response.usage_metadata.prompt_token_count
                    completion_tokens = response.usage_metadata.candidates_token_count
                    total_tokens = response.usage_metadata.total_token_count

            elif model.startswith("openrouter/"):
                actual_model = model.replace("openrouter/", "")
                api_key = self._resolve_api_key("openrouter", event.get("api_key_name"))
                if not api_key:
                    raise ValueError("OpenRouter API key not configured")
                
                # OpenRouter is OpenAI compatible
                or_client = AsyncOpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=api_key
                )
                formatted_msgs = [{"role": m["role"], "content": str(m.get("content", ""))} for m in messages]
                response = await or_client.chat.completions.create(
                    model=actual_model,
                    messages=formatted_msgs,
                    temperature=temperature,
                )

            elif model.startswith("openai/"):
                actual_model = model.replace("openai/", "")
                api_key = self._resolve_api_key("openai", event.get("api_key_name"))
                if not api_key:
                    raise ValueError("OpenAI API key not configured")
                
                openai_client = AsyncOpenAI(
                    api_key=api_key
                )
                formatted_msgs = [{"role": m["role"], "content": str(m.get("content", ""))} for m in messages]
                response = await openai_client.chat.completions.create(
                    model=actual_model,
                    messages=formatted_msgs,
                    temperature=temperature,
                )

            elif model.startswith("atria/"):
                actual_model = model.replace("atria/", "")
                api_key = self._resolve_api_key("atria", event.get("api_key_name"))
                if not api_key:
                    raise ValueError("Atria API key not configured")
                
                # Atria is OpenAI compatible
                atria_client = AsyncOpenAI(
                    base_url="https://api.atria-asi.ai/v1",
                    api_key=api_key
                )
                formatted_msgs = [{"role": m["role"], "content": str(m.get("content", ""))} for m in messages]
                response = await atria_client.chat.completions.create(
                    model=actual_model,
                    messages=formatted_msgs,
                    temperature=temperature,
                )

            else:
                # Default / Local model
                formatted_msgs = [{"role": m["role"], "content": str(m.get("content", ""))} for m in messages]
                
                extra_body = {}
                num_ctx = event.get("num_ctx") or self.config.get("num_ctx")
                if num_ctx:
                    extra_body["options"] = {"num_ctx": int(num_ctx)}
                
                response = await self.local_client.chat.completions.create(
                    model=model,
                    messages=formatted_msgs,
                    temperature=temperature,
                    extra_body=extra_body if extra_body else None
                )

            reply_text = response.choices[0].message.content if hasattr(response, "choices") and response.choices else ""
            
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            cache_read_tokens = 0
            cache_write_tokens = 0
            
            if hasattr(response, "usage") and response.usage:
                prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0
                total_tokens = getattr(response.usage, "total_tokens", 0) or 0
                
                if hasattr(response.usage, "prompt_tokens_details") and response.usage.prompt_tokens_details:
                    cache_read_tokens = getattr(response.usage.prompt_tokens_details, "cached_tokens", 0) or 0
                
                if hasattr(response.usage, "model_extra") and response.usage.model_extra:
                    cache_read_tokens = response.usage.model_extra.get("cache_read_tokens", cache_read_tokens)
                    cache_write_tokens = response.usage.model_extra.get("cache_write_tokens", cache_write_tokens)

            import json
            import os
            from datetime import datetime
            
            # --- Registry Cost Calculation & Logging ---
            registry_path = os.path.join(os.getcwd(), "data", "llm_registry.json")
            cost_usd = 0.0
            pricing = {}
            provider_prefix = model.split("/")[0] if "/" in model else "local"
            
            if os.path.exists(registry_path):
                try:
                    with open(registry_path, "r", encoding="utf-8") as rf:
                        reg = json.load(rf)
                    model_data = reg.get("models", {}).get(model, {})
                    pricing = model_data.get("pricing", {})
                    p_cost = pricing.get("prompt", 0) * max(0, prompt_tokens - cache_read_tokens)
                    c_cost = pricing.get("completion", 0) * completion_tokens
                    cr_cost = pricing.get("cache_read", 0) * cache_read_tokens
                    cw_cost = pricing.get("cache_write", 0) * cache_write_tokens
                    cost_usd = p_cost + c_cost + cr_cost + cw_cost
                except Exception:
                    pass
            
            log_record = {
                "timestamp": datetime.now(UTC).isoformat(),
                "provider": provider_prefix,
                "key_name": event.get("api_key_name", "default"),
                "requested_model": model,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_write_tokens": cache_write_tokens
                },
                "snapshot_pricing": pricing,
                "calculated_cost_usd": cost_usd
            }
            
            try:
                logs_dir = os.path.join(os.getcwd(), "data", "logs")
                os.makedirs(logs_dir, exist_ok=True)
                with open(os.path.join(logs_dir, "llm_usage.jsonl"), "a", encoding="utf-8") as wf:
                    wf.write(json.dumps(log_record, ensure_ascii=False) + "\n")
            except Exception:
                pass

            latency = time.time() - start_time
            
            await self.emit_log(
                f"Request {request_id} completed in {latency:.2f}s. "
                f"Tokens: {prompt_tokens} prompt, {completion_tokens} completion, {total_tokens} total. "
                f"Cost: ${cost_usd:.6f}"
            )
            
            await self.event_bus.publish({
                "type": "llm_response",
                "source": self.__class__.__name__,
                "request_id": request_id,
                "reply": reply_text,
                "model": model,
                "timestamp": datetime.now(UTC).isoformat(),
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens
                },
                "latency_sec": latency
            })
            
        except Exception as e:  # noqa: BLE001
            latency = time.time() - start_time
            error_msg = f"Request {request_id} failed after {latency:.2f}s: {e!s}"
            await self.emit_log(error_msg, level="ERROR")
            
            await self.event_bus.publish({
                "type": "llm_response_error",
                "source": self.__class__.__name__,
                "request_id": request_id,
                "error": str(e)
            })
