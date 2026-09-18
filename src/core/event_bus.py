import asyncio
from typing import Callable, Coroutine, Any, List

class EventBus:
    """Простая шина событий для обмена сообщениями между ядром и плагинами."""
    def __init__(self):
        self.subscribers: List[Callable[[dict], Coroutine[Any, Any, None]]] = []

    def subscribe(self, callback: Callable[[dict], Coroutine[Any, Any, None]]):
        self.subscribers.append(callback)

    async def publish(self, event: dict):
        # Вызываем всех подписчиков параллельно
        if not self.subscribers:
            return
        
        tasks = [asyncio.create_task(sub(event)) for sub in self.subscribers]
        await asyncio.gather(*tasks, return_exceptions=True)
