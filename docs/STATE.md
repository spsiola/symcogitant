# Текущее состояние проекта

- **Версия:** 0.1.8
- **Текущая фаза:** Полноценный сборщик реестра LLM (SystemMonitorReflex), точный трекинг биллинга в LLMReflex и улучшенный интерфейс мониторинга.
- **Ключевые компоненты:**
  - Описание архитектуры (`docs/ARCHITECTURE.md`)
  - План развития (`docs/ROADMAP.md`)
  - Память и промпты (`data/memory/`)
  - Правила агента (`AGENTS.md`)
  - **Ядро**: `symcogitant.py`, `src/core/` (EventBus, BasePlugin)
  - **Плагины**: 
    - `web_interface` (FastAPI + WebSockets, динамические вкладки)
    - `media_telegram_user` (Telethon, перехват сообщений, реакций и удалений)
  - **Рефлексы**: 
    - `system_monitor` (Метрики CPU/RAM, 3-уровневый цикл мониторинга реестра LLM)
    - `llm_reflex` (Клиент LLM с учетом биллинга и логированием в llm_usage.jsonl)
