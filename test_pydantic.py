import json
from src.plugins.talks_manager.models import TalkSettings

data = {
    "preferred_model_tier": "smart",
    "context_window_size": 100,
    "context_schema": {
        "identity": "You are a test identity"
    },
    "trigger_on_every_message": True,
    "trigger_on_mention": True,
    "trigger_on_reply": True,
    "mention_aliases": [],
    "background_read_interval_seconds": 0,
    "system_prompt": "",
    "primary_model": "atria/Atria-Dawn-Preview#symcogitant",
    "fallback_model": "",
    "append_llm_signature": True,
    "llm_signature_template": "test",
    "allowed_extra_tools": []
}

try:
    ts = TalkSettings(**data)
    print("SUCCESS")
    print(ts.context_schema)
    print(ts.model_dump()["context_schema"])
except Exception as e:
    print(f"FAILED: {e}")
