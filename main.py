from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from telethon import TelegramClient

from bot import Notifier, build_router
from config import load_config
from db import Database
from watcher import GiftWatcher


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = load_config()
    db = Database(cfg.db_path)

    client = TelegramClient(cfg.session, cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Сессия не авторизована. Сначала запусти: python login.py")

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    notifier = Notifier(bot, db)
    watcher = GiftWatcher(
        client,
        db,
        on_new=notifier.enqueue,
        rps=cfg.rps,
        refresh_minutes=cfg.refresh_minutes,
        extra_collections=cfg.extra_collections,
    )

    dp = Dispatcher()
    dp.include_router(build_router(db, watcher))

    tasks = [
        asyncio.create_task(notifier.run()),
        asyncio.create_task(watcher.run_forever()),
    ]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
