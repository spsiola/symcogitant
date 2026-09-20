# Текущее состояние проекта

- **Версия:** 0.1.6
- **Текущая фаза:** Реализовано базовое ядро, плагин веб-интерфейса, системный рефлекс, базовая интеграция с Telegram и рефлекс общения с LLM (базовый клиент).
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
    - `system_monitor` (CPU/RAM/LLM endpoints)
    - `llm_reflex` (Асинхронный клиент общения с LLM)
