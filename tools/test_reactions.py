import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import UpdateMessageReactions

async def main():
    load_dotenv("data/.env")
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    client = TelegramClient("data/telegram_user", int(api_id), api_hash)
    await client.start()
    
    print(">>> СЛУШАЮ 60 СЕКУНД. ПОЖАЛУЙСТА, ПОСТАВЬТЕ ИЛИ УБЕРИТЕ РЕАКЦИЮ НА СООБЩЕНИЕ В ТЕЛЕГРАМЕ! <<<")
    
    @client.on(events.Raw)
    async def raw_handler(update):
        if "Reaction" in type(update).__name__ or "Edit" in type(update).__name__:
            print(f"RAW UPDATE: {type(update).__name__}")
            print(update.stringify())
            
    await asyncio.sleep(60)
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
