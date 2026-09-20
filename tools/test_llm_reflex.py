import asyncio
import logging
from src.core.event_bus import EventBus
from src.reflexes.llm_reflex.reflex import LLMReflex

logging.basicConfig(level=logging.INFO)

async def test_llm_reflex():
    bus = EventBus()
    
    # Конфиг для Ollama (локально)
    config = {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "ollama",
        "default_model": "llama3"
    }
    
    reflex = LLMReflex(config, bus)
    
    response_received = asyncio.Event()

    async def on_response(event):
        if event.get("type") in ("llm_response", "llm_response_error"):
            print("Received from LLM:", event)
            response_received.set()
        elif event.get("type") == "log":
            print(f"LOG [{event.get('source')}]: {event.get('message')}")

    bus.subscribe(on_response)
    
    await reflex.start()
    
    # Отправляем тестовый запрос
    await bus.publish({
        "type": "llm_request",
        "request_id": "test_001",
        "messages": [{"role": "user", "content": "Скажи 'Привет' и ничего больше."}]
    })
    
    try:
        await asyncio.wait_for(response_received.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        print("Timeout waiting for LLM response.")
        
    await reflex.stop()

if __name__ == "__main__":
    asyncio.run(test_llm_reflex())
