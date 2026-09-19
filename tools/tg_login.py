import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient

async def main():
    print("=== Telegram Login Script ===")
    
    # Убеждаемся, что папка data существует
    if not os.path.exists("data"):
        print("Создаю директорию 'data/'...")
        os.makedirs("data")

    # Загружаем переменные окружения
    env_path = os.path.join("data", ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        print(f"ВНИМАНИЕ: Файл {env_path} не найден.")

    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")

    if not api_id or not api_hash:
        print("ОШИБКА: TG_API_ID и TG_API_HASH должны быть прописаны в файле data/.env")
        print("Пример содержимого data/.env:")
        print("TG_API_ID=123456")
        print("TG_API_HASH=abcdef1234567890")
        return

    session_path = os.path.join("data", "telegram_user")
    
    # Инициализация клиента
    print(f"Инициализация TelegramClient. Файл сессии: {session_path}.session")
    client = TelegramClient(session_path, int(api_id), api_hash)
    
    # Интерактивный логин (попросит номер телефона и код из Telegram)
    await client.start()
    
    print("\nУСПЕХ! Вы авторизованы.")
    print(f"Файл сессии сохранен в: {session_path}.session")
    print("Теперь вы можете запускать Symcogitant, и MediaTelegramUserPlugin подхватит эту сессию.")
    
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
