"""Бот (aiogram 3): подписка, фильтры и рассылка новых улучшенных подарков."""
from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import LinkPreviewOptions, Message

from db import Database
from models import GiftInfo, UserFilter, matches

log = logging.getLogger("bot")


def _pct(permille: int | None) -> str:
    if permille is None:
        return ""
    return f" ({permille / 10:g}%)"


def _owner_lines(g: GiftInfo) -> list[str]:
    """Владелец + отдельная строка с юзернеймом."""
    esc = html.escape
    if g.owner_username:
        name = esc(g.owner) if g.owner else f"@{esc(g.owner_username)}"
        return [
            f'Владелец: <a href="https://t.me/{esc(g.owner_username)}">{name}</a>',
            f"Юз: @{esc(g.owner_username)}",
        ]
    if g.owner_id:
        name = esc(g.owner) if g.owner else "владелец"
        # id известен, но username нет: либо его у человека нет, либо Telegram его не отдал
        return [f'Владелец: <a href="tg://user?id={g.owner_id}">{name}</a>', "Юз: нет или скрыт"]
    if g.owner:
        return [f"Владелец: {esc(g.owner)}"]  # Telegram отдаёт только имя, без id и юза
    return []


def format_gift(g: GiftInfo) -> str:
    esc = html.escape
    total = f" из {g.total}" if g.total else ""
    lines = [f"🎁 <b>{esc(g.collection)}</b> #{g.num}{total}", ""]
    if g.model:
        lines.append(f"Модель: {esc(g.model)}{_pct(g.model_permille)}")
    if g.backdrop:
        lines.append(f"Фон: {esc(g.backdrop)}{_pct(g.backdrop_permille)}")
    if g.pattern:
        lines.append(f"Узор: {esc(g.pattern)}{_pct(g.pattern_permille)}")
    lines += _owner_lines(g)
    lines += ["", f'<a href="{g.url}">{esc(g.url.removeprefix("https://"))}</a>']
    return "\n".join(lines)


def format_filters(f: UserFilter) -> str:
    cols = ", ".join(f.collections) if f.collections else "все"
    num = f"≤ {f.max_num}" if f.max_num is not None else "любой"
    rar = f"≤ {f.max_rarity / 10:g}%" if f.max_rarity is not None else "любая"
    bds = ", ".join(f.backdrops) if f.backdrops else "любой"
    orig = "только с подписью «от кого / кому»" if f.only_original else "любые"
    state = "включены ✅" if f.active else "выключены ⏸ (/start чтобы включить)"
    return (
        f"Уведомления: {state}\n"
        f"Коллекции: {cols}\n"
        f"Номер подарка: {num}\n"
        f"Редкость модели: {rar}\n"
        f"Фон: {bds}\n"
        f"Подпись: {orig}"
    )


class Notifier:
    """Очередь рассылки: watcher кладёт подарки, отдельная задача шлёт их подписчикам."""

    def __init__(self, bot: Bot, db: Database):
        self.bot = bot
        self.db = db
        self.queue: asyncio.Queue[GiftInfo] = asyncio.Queue()

    def enqueue(self, gift: GiftInfo) -> None:
        self.queue.put_nowait(gift)

    async def run(self) -> None:
        while True:
            gift = await self.queue.get()
            try:
                await self._broadcast(gift)
            except Exception:
                log.exception("Ошибка рассылки %s", gift.slug)

    async def _broadcast(self, gift: GiftInfo) -> None:
        text = format_gift(gift)
        for f in self.db.active_users():
            if matches(f, gift):
                await self._send(f.chat_id, text, gift.url)
                await asyncio.sleep(0.05)  # ~20 сообщений/с, в рамках лимитов

    async def _send(self, chat_id: int, text: str, url: str, retry: bool = True) -> None:
        try:
            await self.bot.send_message(
                chat_id,
                text,
                link_preview_options=LinkPreviewOptions(
                    url=url, show_above_text=True, prefer_large_media=True
                ),
            )
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            if retry:
                await self._send(chat_id, text, url, retry=False)
        except TelegramForbiddenError:
            self.db.update_user(chat_id, active=False)  # пользователь заблокировал бота
        except TelegramBadRequest:
            log.exception("Не удалось отправить в %s", chat_id)


def build_router(db: Database, watcher) -> Router:
    r = Router()

    @r.message(Command("start"))
    async def start(m: Message):
        f = db.subscribe(m.chat.id)
        await m.answer(
            "Привет! Я присылаю только что улучшенные подарки Telegram.\n\n"
            + format_filters(f)
            + "\n\n"
            "/filters — текущие фильтры\n"
            "/list — список коллекций\n"
            "/collections Plush Pepe, Durov's Cap — только эти коллекции\n"
            "/collections all — все коллекции\n"
            "/maxnum 1000 — только номера ≤ 1000 (/maxnum off)\n"
            "/rarity 2 — только модели с редкостью ≤ 2% (/rarity off)\n"
            "/bg black — только с таким фоном, можно несколько: /bg black, midnight blue (/bg off)\n"
            "/original on — только подарки с подписью «Подарок от … для …» (/original off)\n"
            "/reset — сбросить фильтры\n"
            "/stop — выключить уведомления"
        )

    @r.message(Command("stop"))
    async def stop(m: Message):
        if db.get_user(m.chat.id):
            db.update_user(m.chat.id, active=False)
        await m.answer("Уведомления выключены. /start — включить снова.")

    @r.message(Command("filters"))
    async def filters(m: Message):
        f = db.get_user(m.chat.id)
        await m.answer(format_filters(f) if f else "Сначала нажми /start")

    @r.message(Command("reset"))
    async def reset(m: Message):
        if not db.get_user(m.chat.id):
            return await m.answer("Сначала нажми /start")
        db.reset_filters(m.chat.id)
        await m.answer("Фильтры сброшены.\n\n" + format_filters(db.get_user(m.chat.id)))

    @r.message(Command("list"))
    async def list_collections(m: Message):
        names = sorted(watcher.totals)
        if not names:
            return await m.answer("Список коллекций ещё загружается, попробуй через минуту.")
        text = "Отслеживаемые коллекции:\n" + "\n".join(f"• {html.escape(n)}" for n in names)
        for i in range(0, len(text), 4000):  # лимит Telegram — 4096 символов
            await m.answer(text[i : i + 4000])

    @r.message(Command("collections"))
    async def set_collections(m: Message, command: CommandObject):
        if not db.get_user(m.chat.id):
            return await m.answer("Сначала нажми /start")
        arg = (command.args or "").strip()
        if not arg or arg.lower() in {"all", "все", "off"}:
            db.update_user(m.chat.id, collections=[])
        else:
            names = [x.strip() for x in arg.split(",") if x.strip()]
            db.update_user(m.chat.id, collections=names)
        await m.answer(format_filters(db.get_user(m.chat.id)))

    @r.message(Command("maxnum"))
    async def set_maxnum(m: Message, command: CommandObject):
        if not db.get_user(m.chat.id):
            return await m.answer("Сначала нажми /start")
        arg = (command.args or "").strip().lower()
        if arg in {"", "off", "нет"}:
            db.update_user(m.chat.id, max_num=None)
        elif arg.isdigit() and int(arg) > 0:
            db.update_user(m.chat.id, max_num=int(arg))
        else:
            return await m.answer("Пример: /maxnum 1000 или /maxnum off")
        await m.answer(format_filters(db.get_user(m.chat.id)))

    @r.message(Command("rarity"))
    async def set_rarity(m: Message, command: CommandObject):
        if not db.get_user(m.chat.id):
            return await m.answer("Сначала нажми /start")
        arg = (command.args or "").strip().lower().replace(",", ".").rstrip("%")
        if arg in {"", "off", "нет"}:
            db.update_user(m.chat.id, max_rarity=None)
        else:
            try:
                pct = float(arg)
                if pct <= 0:
                    raise ValueError
            except ValueError:
                return await m.answer("Пример: /rarity 2 (модели с редкостью ≤ 2%) или /rarity off")
            db.update_user(m.chat.id, max_rarity=round(pct * 10))
        await m.answer(format_filters(db.get_user(m.chat.id)))

    @r.message(Command("bg"))
    async def set_backdrop(m: Message, command: CommandObject):
        if not db.get_user(m.chat.id):
            return await m.answer("Сначала нажми /start")
        arg = (command.args or "").strip()
        if arg.lower() in {"", "off", "all", "нет", "все"}:
            db.update_user(m.chat.id, backdrops=[])
        else:
            names = [x.strip() for x in arg.split(",") if x.strip()]
            db.update_user(m.chat.id, backdrops=names)
        await m.answer(
            format_filters(db.get_user(m.chat.id))
            + "\n\nСовпадение по части названия: «black» подойдёт к «Black» и «Onyx Black»."
        )

    @r.message(Command("original"))
    async def set_original(m: Message, command: CommandObject):
        if not db.get_user(m.chat.id):
            return await m.answer("Сначала нажми /start")
        arg = (command.args or "").strip().lower()
        if arg in {"on", "вкл", "1", "да"}:
            db.update_user(m.chat.id, only_original=True)
        elif arg in {"off", "выкл", "0", "нет", ""}:
            db.update_user(m.chat.id, only_original=False)
        else:
            return await m.answer("Пример: /original on или /original off")
        await m.answer(format_filters(db.get_user(m.chat.id)))

    return r
