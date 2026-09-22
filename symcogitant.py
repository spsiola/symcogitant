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

    def get_plugin(self, name: str) -> BasePlugin | None:
        for p in self.plugins:
            if p.__class__.__name__ == name:
                return p
        return None

    def _load_plugin(self, plugin_config: dict) -> BasePlugin | None:
        try:
            module_name = plugin_config['module']
            class_name = plugin_config['class']
            
            module = importlib.import_module(module_name)
            plugin_class = getattr(module, class_name)
            
            return plugin_class(plugin_config.get('config', {}), self.event_bus, core=self)
        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_config.get('class', 'Unknown')}: {e}")
            return None

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
                if plugin:
                    self.plugins.append(plugin)
                    try:
                        await plugin.start()
                    except Exception as e:
                        logger.error(f"Failed to start plugin {p_cfg['class']}: {e}")

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
                plugin_statuses = []
                for plugin in self.plugins:
                    status_info = plugin.get_status()
                    plugin_statuses.append(status_info)
                    
                # Отправляем единое событие статуса системы
                asyncio.create_task(self.event_bus.publish({
                    "type": "harness_status",
                    "source": "harness core",
                    "plugins": plugin_statuses
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
