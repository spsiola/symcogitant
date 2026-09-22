# Текущее состояние проекта

- **Версия:** 0.4.0
- **Текущая фаза:** Разработка интеллектуальных агентов: реализована динамическая маршрутизация моделей LLM (ModelSelectorWorker) и продвинутые триггеры для Telegram групп.
- **Ключевые компоненты:**
  - Описание архитектуры (`docs/ARCHITECTURE.md`)
  - План развития (`docs/ROADMAP.md`)
  - Память и промпты (`data/memory/`)
  - Правила агента (`AGENTS.md`)
  - **Ядро**: `symcogitant.py`, `src/core/` (EventBus, базовые классы BaseModule, BasePlugin, BaseAgentPlugin, BaseDaemon, BaseWorker, BaseInterceptor)
  - **Плагины** (`src/plugins/`): 
    - `web_interface` (FastAPI + WebSockets, динамические вкладки)
    - `media_telegram_user` (Telethon, перехват сообщений, реакций и удалений, отправка ответов)
    - `telegram_private_dispatcher` (Маршрутизатор приватных диалогов с поддержкой сессий, Sliding Window и таймаутов)
    - `telegram_group_dispatcher` (Маршрутизатор групповых чатов, активация по ключевым словам "Зеленый"/"ИИ")
  - **Демоны** (`src/daemons/`): 
    - `system_monitor` (Метрики CPU/RAM, 3-уровневый цикл мониторинга реестра LLM)
  - **Воркеры** (`src/workers/`):
    - `llm_worker` (Клиент LLM с учетом биллинга и логированием в llm_usage.jsonl)
    - `model_selector` (Динамический выбор LLM-модели на базе правил роутинга)
