import os
import json
import uuid
import time
import hashlib
from pathlib import Path

class TelegramOutbox:
    """
    Легковесный, но надежный Outbox для гарантированной отправки сообщений в Telegram.
    Хранит незавершенные намерения отправки (intents) в виде JSON-файлов.
    Поддерживает идемпотентность через детерминированный генератор random_id для Telethon.
    """
    
    def __init__(self, root_dir="data/transports/tg/outbox"):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
    
    def add_intent(self, chat_id: int, text: str, reply_to: int | None = None) -> str:
        intent_id = str(uuid.uuid4())
        intent = {
            "id": intent_id,
            "chat_id": chat_id,
            "text": text,
            "reply_to": reply_to,
            "status": "pending",
            "attempts": 0,
            "next_attempt_at": time.time(),
            "created_at": time.time(),
            "last_error": ""
        }
        self._save_intent(intent_id, intent)
        return intent_id

    def get_due_intents(self) -> list[dict]:
        """Возвращает все pending/retry интенты, время которых пришло."""
        due = []
        now = time.time()
        for p in self.root_dir.glob("*.json"):
            try:
                intent = json.loads(p.read_text(encoding="utf-8"))
                if intent.get("status") in ("pending", "retry"):
                    if intent.get("next_attempt_at", 0) <= now:
                        due.append(intent)
            except Exception:
                pass
        # Сортируем по времени создания, чтобы отправлять в правильном порядке
        return sorted(due, key=lambda x: x.get("created_at", 0))

    def _save_intent(self, intent_id: str, intent: dict):
        """Атомарно сохраняет файл."""
        path = self.root_dir / f"{intent_id}.json"
        temp = path.with_suffix(f".tmp.{os.getpid()}")
        with open(temp, 'w', encoding="utf-8") as f:
            json.dump(intent, f, ensure_ascii=False, indent=2)
        temp.replace(path)

    def mark_accepted(self, intent_id: str):
        """Удаляет файл, когда сообщение успешно принято серверами Telegram."""
        path = self.root_dir / f"{intent_id}.json"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def mark_retry(self, intent_id: str, error: str, flood_wait_seconds: int = 0):
        """Помечает сообщение для повторной отправки с exponential backoff."""
        path = self.root_dir / f"{intent_id}.json"
        if not path.exists():
            return
            
        try:
            intent = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
            
        intent["attempts"] = intent.get("attempts", 0) + 1
        intent["status"] = "retry"
        intent["last_error"] = str(error)
        
        # Если это FloodWait (Rate Limit), ждем столько, сколько просит сервер Telegram
        if flood_wait_seconds > 0:
            delay = flood_wait_seconds
        else:
            # Экспоненциальная задержка: 5s, 10s, 20s, 40s... макс 1 час
            delay = min(3600, 5 * (2 ** (intent["attempts"] - 1)))
            
        intent["next_attempt_at"] = time.time() + delay
        self._save_intent(intent_id, intent)

    def stable_random_id(self, intent_id: str) -> int:
        """
        Возвращает детерминированный, ненулевой знаковый int64 random_id для MTProto.
        Гарантирует идемпотентность: если мы попытаемся отправить одно и то же сообщение
        дважды из-за таймаута сети, Telegram распознает дубликат по этому ID.
        """
        digest = hashlib.sha256(
            b"symcogitant.telegram.outbox.random-id.v1\0" + intent_id.encode("utf-8")
        ).digest()
        unsigned = int.from_bytes(digest[:8], "big", signed=False)
        signed = unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned
        return signed or 1
