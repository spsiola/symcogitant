import asyncio
from abc import ABC, abstractmethod
import time
from typing import Any, Dict

class BasePlugin(ABC):
    """
    Базовый класс для всех плагинов Symcogitant.
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any):
        self.config = config
        self.event_bus = event_bus
        self.running = False
        self.task: asyncio.Task | None = None
        self.start_time: float | None = None

    @abstractmethod
    async def run(self):
        """Основной цикл или логика плагина."""
        pass

    async def start(self):
        """Запускает плагин как asyncio.Task."""
        if not self.running:
            self.running = True
            self.start_time = time.time()
            self.task = asyncio.create_task(self.run())
    
    async def stop(self):
        """Останавливает плагин."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def emit_log(self, message: str, level: str = "INFO"):
        """Вспомогательный метод для отправки логов в главную шину событий."""
        await self.event_bus.publish({
            "type": "log",
            "source": self.__class__.__name__,
            "level": level,
            "message": message
        })

    def get_status(self) -> dict:
        """Возвращает текущий статус плагина."""
        if self.running and self.start_time:
            uptime = int(time.time() - self.start_time)
            return {"status": "alive", "uptime": uptime}
        return {"status": "stopped", "uptime": 0}
