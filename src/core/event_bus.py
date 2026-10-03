import asyncio
from typing import Callable, Coroutine, Any, List

class EventBus:
    """Простая шина событий для обмена сообщениями между ядром и плагинами."""
    def __init__(self):
        self.subscribers: List[Callable[[dict], Coroutine[Any, Any, None]]] = []
        self.interceptors: List[Callable[[dict], Coroutine[Any, Any, bool]]] = []

    def subscribe(self, callback: Callable[[dict], Coroutine[Any, Any, None]]):
        self.subscribers.append(callback)

    def add_interceptor(self, callback: Callable[[dict], Coroutine[Any, Any, bool]]):
        """Регистрирует interceptor. Если он вернет False, событие будет заблокировано."""
        self.interceptors.append(callback)

    async def publish(self, event: dict):
        # 1. Пропускаем через interceptors (middleware chain)
        for interceptor in self.interceptors:
            try:
                is_allowed = await interceptor(event)
                if not is_allowed:
                    return # Событие заблокировано
            except Exception as e:
                print(f"[EventBus] Interceptor error: {e}")
                return

        # 2. Вызываем всех подписчиков параллельно
        if not self.subscribers:
            return
        
        tasks = [asyncio.create_task(sub(event)) for sub in self.subscribers]
        await asyncio.gather(*tasks, return_exceptions=True)
