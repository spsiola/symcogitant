import asyncio
import importlib
import logging
import signal

import yaml

from src.core.event_bus import EventBus
from src.core.base import BaseModule, BasePlugin, BaseDaemon, BaseWorker, BaseInterceptor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Symcogitant")

class SymcogitantCore:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.event_bus = EventBus()
        self._stop_event = asyncio.Event()
        
        self.plugins: list[BasePlugin] = []
        self.daemons: list[BaseDaemon] = []
        self.workers: list[BaseWorker] = []
        self.interceptors: list[BaseInterceptor] = []
        self.all_modules: list[BaseModule] = []

    def load_config(self) -> dict:
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def get_module(self, name: str) -> BaseModule | None:
        for m in self.all_modules:
            if m.__class__.__name__ == name:
                return m
        return None

    def get_plugin(self, name: str) -> BasePlugin | None:
        return self.get_module(name)

    def get_daemon(self, name: str) -> BaseDaemon | None:
        return self.get_module(name)

    def get_worker(self, name: str) -> BaseWorker | None:
        return self.get_module(name)

    def _load_module(self, config: dict, expected_type: type) -> BaseModule | None:
        try:
            module_name = config['module']
            class_name = config['class']
            
            module = importlib.import_module(module_name)
            module_class = getattr(module, class_name)
            
            if not issubclass(module_class, expected_type):
                logger.error(f"Class {class_name} is not a subclass of {expected_type.__name__}")
                return None
            
            return module_class(config.get('config', {}), self.event_bus, core=self)
        except Exception as e:
            logger.error(f"Failed to load module {config.get('class', 'Unknown')}: {e}")
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

    async def _start_modules(self, module_list: list, config_list: list | None, expected_type: type, category_name: str):
        if config_list is None:
            return
        for cfg in config_list:
            if cfg.get('enabled', True):
                logger.info(f"Loading {category_name}: {cfg['class']}")
                module_instance = self._load_module(cfg, expected_type)
                if module_instance:
                    module_list.append(module_instance)
                    self.all_modules.append(module_instance)
                    try:
                        await module_instance.start()
                    except Exception as e:
                        logger.error(f"Failed to start {category_name} {cfg['class']}: {e}")

    async def start(self):
        config = self.load_config()
        self.event_bus.subscribe(self._log_subscriber)

        # Initialization
        await self._start_modules(self.daemons, config.get('daemons', []), BaseDaemon, "daemon")
        await self._start_modules(self.workers, config.get('workers', []), BaseWorker, "worker")
        await self._start_modules(self.interceptors, config.get('interceptors', []), BaseInterceptor, "interceptor")
        await self._start_modules(self.plugins, config.get('plugins', []), BasePlugin, "plugin")

        logger.info("Symcogitant started. Press Ctrl+C to stop.")
        
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop_event.set)

        watchdog_interval = config.get("core", {}).get("watchdog_interval", 30)
        logger.info(f"Watchdog started. Polling every {watchdog_interval} seconds.")
        
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=watchdog_interval)
            except asyncio.TimeoutError:
                harness_status = {
                    "type": "harness_status",
                    "source": "harness core",
                    "daemons": [m.get_status() for m in self.daemons],
                    "workers": [m.get_status() for m in self.workers],
                    "interceptors": [m.get_status() for m in self.interceptors],
                    "plugins": [m.get_status() for m in self.plugins],
                }
                asyncio.create_task(self.event_bus.publish(harness_status))
        
        logger.info("Shutting down...")
        await self.stop()

    async def stop(self):
        for module in self.all_modules:
            await module.stop()
        logger.info("Shutdown complete.")

def main():
    core = SymcogitantCore()
    try:
        asyncio.run(core.start())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
