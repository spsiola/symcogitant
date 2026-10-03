import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv("data/.env")
api_key = os.environ.get("OPENROUTER_API_KEY")

async def test():
    async with httpx.AsyncClient() as client:
        # First call to cache it
        msgs = [{"role": "user", "content": "Tell me a very long story about a cat. " * 50}]
        payload = {
            "model": "anthropic/claude-3.5-sonnet:beta",
            "messages": msgs
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        
        r = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
        print("Raw response JSON:", r.json())

        # Second call to get cache read
        r2 = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
        print("Second raw response JSON:", r2.json())

asyncio.run(test())
