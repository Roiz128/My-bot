"""Разовый вход в юзер-аккаунт (нужен для чтения данных о подарках через MTProto)."""
from telethon.sync import TelegramClient

from config import load_config

cfg = load_config()
with TelegramClient(cfg.session, cfg.api_id, cfg.api_hash) as client:
    me = client.get_me()
    print(f"Готово! Вошёл как {me.first_name} (id {me.id}). Файл сессии сохранён.")
