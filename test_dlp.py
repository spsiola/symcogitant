import os
import json

def load_env_secrets():
    secrets = []
    env_path = os.path.abspath(os.path.join(os.getcwd(), "data", ".env"))
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    _, val = line.split("=", 1)
                    val = val.strip().strip("'\"")
                    if len(val) > 4:
                        secrets.append(val)
    return secrets

secrets = load_env_secrets()
print("Secrets:", secrets)

messages = [
    {"role": "system", "content": "You are operating in the talk room 'Sergei Σψ' (Type: priv, Status: active).\nHere is the history of the conversation formatted as a script.\nAnalyze the context and use the available tools to respond if necessary.\n\nRules for this room:\nТы общаешься тет-а-тет с пользователем. Отвечай прямо на каждое его сообщение. Если это установка или снятие реакции, удаление или редактирование сообщения, реши стоит ли отвечать, и если нет, то пришли [NO ANSWER]"},
    {"role": "user", "content": "[2026-10-03T19:46:10+00:00] User (Sergei Σψ):\nПоследнее сообщение какое?"}
]

for msg in messages:
    content = msg.get("content", "")
    for secret in secrets:
        if secret in content:
            print(f"Match found! Secret '{secret}' found in content.")
