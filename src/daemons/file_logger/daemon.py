import asyncio
import os
import datetime
import json
import aiofiles
from typing import Any
from src.core.base import BaseDaemon

class FileLoggerDaemon(BaseDaemon):
    def __init__(self, config: dict, event_bus: Any, core: Any = None):
        super().__init__(config, event_bus, core)
        self.description = "Фоновый логгер событий в файлы с дневной ротацией"
        self.queue = asyncio.Queue()
        self.base_log_dir = "data/logs"
        self.ignored_types = config.get("ignored_types", ["harness_status", "metric"])

    async def _handle_event(self, event: dict):
        if event.get("type") in self.ignored_types:
            return
        await self.queue.put(event)

    async def run(self):
        # Подписываемся на шину событий (так как on_start не вызывается базовым классом при наличии run)
        self.event_bus.subscribe(self._handle_event)
        # Главный цикл демона
        while self.running:
            try:
                # Ждем события с таймаутом, чтобы иметь возможность выйти при остановке
                event = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                await self._process_event(event)
                self.queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Не используем emit_log, чтобы не зациклить логгер
                print(f"Error in FileLoggerDaemon: {e}")
                
    async def _process_event(self, event: dict):
        source = event.get("source", "system")
        # Очищаем source от возможных спецсимволов для безопасности пути
        safe_source = "".join(c for c in source if c.isalnum() or c in ('_', '-'))
        if not safe_source:
            safe_source = "unknown"
            
        # Папка назначения
        log_dir = os.path.join(self.base_log_dir, safe_source)
        os.makedirs(log_dir, exist_ok=True)
        
        # Имя файла с ротацией по дням, расширение .jsonl
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(log_dir, f"{safe_source}_{date_str}.jsonl")
        
        # Копируем событие и инжектим таймстемп
        log_data = event.copy()
        log_data["timestamp"] = datetime.datetime.now().isoformat()
        
        # Сериализуем весь объект в одну строку JSON
        log_line = json.dumps(log_data, ensure_ascii=False) + "\n"
            
        async with aiofiles.open(log_file, mode='a', encoding='utf-8') as f:
            await f.write(log_line)
