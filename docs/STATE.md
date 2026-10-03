# Текущее состояние проекта

- **Версия:** 0.5.4
- **Текущая фаза:** Масштабный рефакторинг завершен. TalksManager: реализована имитация человекоподобного набора текста («печатает...») и отправка сигналов «прочитано», а также отслеживание статуса присутствия собеседников. Исправлено слияние конфигурационных массивов (deep_merge) в загрузчике. LLMWorker: реализована поддержка Anthropic Prompt Caching (OpenRouter / Direct).
- **Ключевые компоненты:**
  - Описание архитектуры (`docs/ARCHITECTURE.md`)
  - План развития (`docs/ROADMAP.md`)
  - Память и промпты (`data/memory/`)
  - Правила агента (`AGENTS.md`)
  - **Ядро**: `symcogitant.py`, `src/core/` (EventBus, базовые классы BaseModule, BasePlugin, BaseAgentPlugin, BaseDaemon, BaseWorker, BaseInterceptor)
  - **Плагины** (`src/plugins/`): 
    - `web_interface` (FastAPI + WebSockets, динамические вкладки)
    - `telegram_transport` (MTProto/Telethon адаптер — только ввод-вывод: события и отправка, перехват сырых событий реакций/удалений)
    - `talks_manager` (Универсальное ядро диалогов. Оболочка `Talk` с сохранением стейта, формированием промпта-пьесы и изоляцией данных по каждой комнате)
  - **Демоны** (`src/daemons/`): 
    - `system_monitor` (Метрики CPU/RAM, 3-уровневый цикл мониторинга реестра LLM)
    - `file_logger` (Глобальное логирование всех событий `EventBus` в файлы формата JSONL)
  - **Воркеры** (`src/workers/`):
    - `llm_worker` (Клиент LLM с учетом биллинга и логированием в llm_usage.jsonl)
    - `model_selector` (Динамический выбор LLM-модели на базе правил роутинга)
