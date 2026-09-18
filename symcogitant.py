import asyncio
import importlib
import logging
import signal

import yaml

from src.core.event_bus import EventBus
from src.core.plugin import BasePlugin

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Symcogitant")

class SymcogitantCore:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.event_bus = EventBus()
        self.plugins: list[BasePlugin] = []
        self._stop_event = asyncio.Event()

    def load_config(self) -> dict:
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _load_plugin(self, plugin_config: dict) -> BasePlugin:
        module_name = plugin_config['module']
        class_name = plugin_config['class']
        
        module = importlib.import_module(module_name)
        plugin_class = getattr(module, class_name)
        
        return plugin_class(plugin_config.get('config', {}), self.event_bus)

    async def _log_subscriber(self, event: dict):
        if event.get("type") == "log":
            msg = f"[{event.get('source')}] {event.get('message')}"
            level = event.get('level', 'INFO').upper()
            if level == 'ERROR':
                logger.error(msg)
            elif level == 'WARNING':
                logger.warning(msg)
            else:
                logger.info(msg)

    async def start(self):
        config = self.load_config()
        self.event_bus.subscribe(self._log_subscriber)

        # Инициализация и запуск плагинов
        for p_cfg in config.get('plugins', []):
            if p_cfg.get('enabled', True):
                logger.info(f"Loading plugin: {p_cfg['class']}")
                plugin = self._load_plugin(p_cfg)
                self.plugins.append(plugin)
                await plugin.start()

        logger.info("Symcogitant started. Press Ctrl+C to stop.")
        
        # Настройка обработчиков сигналов для корректного завершения
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop_event.set)

        # Главный цикл (watchdog)
        watchdog_interval = config.get("core", {}).get("watchdog_interval", 30)
        logger.info(f"Watchdog started. Polling plugins every {watchdog_interval} seconds.")
        
        while not self._stop_event.is_set():
            try:
                # Ждем события остановки с таймаутом
                await asyncio.wait_for(self._stop_event.wait(), timeout=watchdog_interval)
            except asyncio.TimeoutError:
                # Таймаут сработал — опрашиваем плагины
                for plugin in self.plugins:
                    status_info = plugin.get_status()
                    plugin_name = plugin.__class__.__name__
                    status_str = f"status: {status_info['status']} {status_info['uptime']} sec"
                    
                    log_msg = f"plugin {plugin_name} -> {status_str}"
                    logger.info(log_msg)
                    
                    # Отправляем лог в шину от имени ядра
                    asyncio.create_task(self.event_bus.publish({
                        "type": "log",
                        "source": "harness core",
                        "level": "INFO",
                        "message": log_msg
                    }))
        
        logger.info("Shutting down...")
        await self.stop()

    async def stop(self):
        for plugin in self.plugins:
            await plugin.stop()
        logger.info("Shutdown complete.")

def main():
    core = SymcogitantCore()
    try:
        asyncio.run(core.start())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
