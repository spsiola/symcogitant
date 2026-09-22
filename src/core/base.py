import asyncio
from abc import ABC, abstractmethod
import time
from typing import Any, Dict

class BaseModule(ABC):
    """
    Базовый класс для всех исполняемых модулей Symcogitant (Плагины, Демоны, Воркеры, Интерцепторы).
    """
    def __init__(self, config: Dict[str, Any], event_bus: Any, core: Any = None):
        self.config = config
        self.event_bus = event_bus
        self.core = core
        self.running = False
        self.task: asyncio.Task | None = None
        self.start_time: float | None = None
        self.description: str = "Описание модуля не задано."

    async def start(self):
        """Запускает модуль. Если определен run(), запускает его как таску."""
        if not self.running:
            self.running = True
            self.start_time = time.time()
            if hasattr(self, 'run') and callable(getattr(self, 'run')):
                self.task = asyncio.create_task(self.run())
            else:
                await self.on_start()

    async def on_start(self):
        """Хук для инициализации (например, подписки на EventBus) для воркеров без бесконечного цикла."""
        pass

    async def stop(self):
        """Останавливает модуль."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def emit_log(self, message: str, level: str = "INFO"):
        """Отправка логов в главную шину событий."""
        await self.event_bus.publish({
            "type": "log",
            "source": self.__class__.__name__,
            "level": level,
            "message": message
        })

    def get_status(self) -> dict:
        """Возвращает текущий статус модуля."""
        status_info = {
            "name": self.__class__.__name__,
            "description": self.description
        }
        if self.running and self.start_time:
            uptime = int(time.time() - self.start_time)
            status_info.update({"status": "alive", "uptime": uptime})
        else:
            status_info.update({"status": "stopped", "uptime": 0})
        return status_info

class BasePlugin(BaseModule):
    """Сенсоры и интерфейсы взаимодействия с пользователем."""
    @abstractmethod
    async def run(self):
        pass

class BaseDaemon(BaseModule):
    """Фоновые процессы, работающие постоянно (мониторинг, гомеостаз)."""
    @abstractmethod
    async def run(self):
        pass

class BaseWorker(BaseModule):
    """Воркеры, работающие по требованию (подписываются на события). Не имеют бесконечного цикла run()."""
    pass

class BaseInterceptor(BaseModule):
    """Пре/пост обработчики (middleware) для пайплайнов."""
    pass
