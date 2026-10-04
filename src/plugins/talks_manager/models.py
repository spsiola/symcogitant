from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum

class ParticipantRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"
    BOT = "bot"
    ASSISTANT = "assistant"

class TalkParticipant(BaseModel):
    id: str
    name: str
    role: ParticipantRole = ParticipantRole.USER
    is_me: bool = False  # Флаг, является ли этот участник самим ИИ-агентом

class AttachmentType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    VOICE = "voice"
    STICKER = "sticker"
    OTHER = "other"

class Attachment(BaseModel):
    type: AttachmentType
    file_id: str
    mime: Optional[str] = None
    size: Optional[int] = None
    is_downloaded: bool = False
    local_path: Optional[str] = None
    caption: Optional[str] = None

class Reaction(BaseModel):
    user_id: str
    emoji: str
    date: Optional[str] = None

class UniversalMessage(BaseModel):
    talk_id: str
    msg_id: str
    protocol: str
    date: str  # ISO format string
    sender: TalkParticipant
    text: str
    attachments: List[Attachment] = Field(default_factory=list)
    reactions: List[Reaction] = Field(default_factory=list)
    is_outgoing: bool = False
    is_deleted: bool = False
    is_edited: bool = False
    edited_date: Optional[str] = None
    reply_to_msg_id: Optional[str] = None
    mentions_me: bool = False
    
    # Для внутренних системных пометок агента, которые не уходят на сервер,
    # например "Агент прочитал это сообщение" или "Внутренние мысли"
    internal_metadata: Dict[str, Any] = Field(default_factory=dict)

class RoomMode(str, Enum):
    ACTIVE = "active"     # Отвечает на триггеры
    OBSERVER = "observer" # Только читает историю, но LLM не триггерится
    QUIET = "quiet"       # Отвечает ТОЛЬКО при прямом упоминании
    FROZEN = "frozen"     # Игнорируется полностью (даже история не пишется)
    DEAD = "dead"         # Бот был удален или чат уничтожен

class TalkProfile(BaseModel):
    title: str = "Unknown Talk"
    talk_type: str = "unknown"
    status: RoomMode = RoomMode.ACTIVE
    tags: List[str] = Field(default_factory=list) # e.g. ["private", "important", "public_group"]
    created_at: str

class TalkSettings(BaseModel):
    model_config = {"extra": "allow"}
    
    # Требования к LLM
    preferred_model_tier: str = "smart"  # 'smart', 'fast', 'cheap'
    context_window_size: int = 100
    
    # Схема сборки контекста
    context_schema: Optional[Dict[str, Any]] = None
    
    # Триггеры
    trigger_on_every_message: bool = False
    trigger_on_mention: bool = True
    trigger_on_reply: bool = True
    mention_aliases: List[str] = Field(default_factory=list)
    
    # Дебаунсинг и человекоподобные задержки (в секундах)
    debounce_delay_min: float = 2.0
    debounce_delay_max: float = 7.0
    
    # Регулярное чтение (Фоновый цикл)
    background_read_interval_seconds: int = 0  # 0 означает отключено
    
    # Системный промпт комнаты
    system_prompt: str = ""
    
    # Явное указание моделей (переопределяет preferred_model_tier)
    primary_model: str = ""
    fallback_model: str = ""
    
    # Добавлять подпись с метриками LLM к сообщениям (не сохраняется в историю)
    append_llm_signature: bool = False
    
    # Шаблон для подписи. Поддерживаемые ключи: {provider}, {req_model}, {act_model}, {in_t}, {cache_pct}, {out_t}
    llm_signature_template: str = "\n\n🤖 {provider}>{req_model} || {act_model} [in/cash_%/out - {in_t} / {cache_pct}% / {out_t} tokens]."
    
    # Дополнительные инструменты (помимо базовых reply, edit и т.д.),
    # которые разрешены модели в рамках этой комнаты (например, 'get_weather', 'get_server_time')
    allowed_extra_tools: List[str] = Field(default_factory=list)

class TransportCapability(BaseModel):
    can_send_text: bool = True
    can_send_media: bool = False
    can_edit: bool = False
    can_delete: bool = False
    can_react: bool = False
    can_download_media: bool = False

class TransportIdentity(BaseModel):
    protocol: str
    agent_id: str
    agent_name: str
