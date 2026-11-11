"""Наблюдатель за улучшенными подарками (Telethon, MTProto).

Идея: у каждого улучшенного подарка есть ссылка t.me/nft/<Коллекция>-<N>, а N растёт
по порядку улучшений внутри коллекции. Значит, «новый улучшенный подарок» — это
подарок с номером last+1. Мы по кругу спрашиваем last+1 у каждой коллекции.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError, RPCError

from models import GiftInfo, slugify

log = logging.getLogger("watcher")

MAX_PROBE = 5_000_000


class RateLimiter:
    def __init__(self, rps: float):
        self.interval = 1.0 / max(rps, 0.1)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = max(now, self._next) + self.interval


def _permille(attr) -> int | None:
    """Редкость атрибута в промилле (10 = 1%). Поддерживает разные версии TL-схемы."""
    v = getattr(attr, "rarity_permille", None)
    if v is not None:
        return int(v)
    r = getattr(attr, "rarity", None)
    p = getattr(r, "permille", None) if r is not None else None
    return int(p) if p is not None else None


def _find_peer(res, peer):
    """Найти пользователя/канал владельца в ответе Telegram (users/chats)."""
    if peer is None:
        return None
    uid = getattr(peer, "user_id", None)
    cid = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
    if uid is not None:
        for u in getattr(res, "users", None) or []:
            if getattr(u, "id", None) == uid:
                return u
    elif cid is not None:
        for c in getattr(res, "chats", None) or []:
            if getattr(c, "id", None) == cid:
                return c
    return None


def _username(entity) -> str | None:
    if entity is None:
        return None
    u = getattr(entity, "username", None)
    if u:
        return u
    for un in getattr(entity, "usernames", None) or []:
        if getattr(un, "active", True) and getattr(un, "username", None):
            return un.username
    return None


def parse_gift(collection: str, g, slug: str, res=None) -> GiftInfo:
    info = GiftInfo(
        collection=getattr(g, "title", None) or collection,
        num=int(getattr(g, "num", 0) or 0),
        slug=getattr(g, "slug", None) or slug,
        issued=getattr(g, "availability_issued", None),
        total=getattr(g, "availability_total", None),
    )
    for a in getattr(g, "attributes", None) or []:
        kind = type(a).__name__
        name = getattr(a, "name", None)
        if kind == "StarGiftAttributeModel":
            info.model, info.model_permille = name, _permille(a)
        elif kind == "StarGiftAttributeBackdrop":
            info.backdrop, info.backdrop_permille = name, _permille(a)
        elif kind == "StarGiftAttributePattern":
            info.pattern, info.pattern_permille = name, _permille(a)
        elif kind == "StarGiftAttributeOriginalDetails":
            info.has_original = True  # у подарка есть подпись «Подарок от … для …»

    info.owner = getattr(g, "owner_name", None)
    owner_peer = getattr(g, "owner_id", None)
    info.owner_id = getattr(owner_peer, "user_id", None)
    entity = _find_peer(res, owner_peer)
    info.owner_username = _username(entity)
    if not info.owner and entity is not None:
        full = " ".join(
            x for x in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if x
        ) or getattr(entity, "title", None)
        info.owner = full or None
    return info


class GiftWatcher:
    def __init__(
        self,
        client: TelegramClient,
        db,
        on_new: Callable[[GiftInfo], None],
        rps: float = 8,
        refresh_minutes: int = 60,
        extra_collections: list[str] | None = None,
    ):
        self.client = client
        self.db = db
        self.on_new = on_new
        self.limiter = RateLimiter(rps)
        self.refresh_s = refresh_minutes * 60
        self.extra = extra_collections or []
        self.totals: dict[str, int | None] = {}   # коллекция -> максимум улучшений
        self.pos: dict[str, int] = db.all_positions()  # коллекция -> последний известный №
        self.retry: set[str] = set()
        # Коллекции с позицией из БД: перед опросом их нужно тихо «догнать» до текущего номера,
        # чтобы не слать то, что улучшили, пока бот был выключен.
        self.need_catchup: set[str] = set(self.pos)

    # ------------------------------------------------------------------ запросы
    async def fetch(self, collection: str, num: int) -> GiftInfo | None:
        """Подарок №num из коллекции или None, если такого ещё нет."""
        slug = f"{slugify(collection)}-{num}"
        while True:
            await self.limiter.wait()
            try:
                res = await self.client(functions.payments.GetUniqueStarGiftRequest(slug=slug))
            except FloodWaitError as e:
                log.warning("FloodWait %ss", e.seconds)
                await asyncio.sleep(e.seconds + 1)
                continue
            except RPCError as e:
                msg = (getattr(e, "message", "") or str(e)).upper()
                if "ALREADY_BURNED" in msg:
                    # номер занят, но подарок сожжён: считаем, что он существует
                    return GiftInfo(collection=collection, num=num, slug=slug, burned=True)
                if "SLUG" in msg or "NOT_FOUND" in msg:
                    return None
                raise
            g = getattr(res, "gift", None)
            if g is None:
                return None
            return parse_gift(collection, g, slug, res)

    async def _resolve_owner(self, gift: GiftInfo) -> None:
        """Если юз владельца не пришёл в ответе Telegram — пробуем достать его отдельно."""
        if gift.owner_username or not gift.owner_id:
            return
        try:
            await self.limiter.wait()
            ent = await self.client.get_entity(gift.owner_id)
        except Exception as e:  # нет в кэше сессии / скрыт / нет доступа
            log.info("Юз владельца id=%s получить не удалось: %s", gift.owner_id, e)
            return
        gift.owner_username = _username(ent)
        if not gift.owner:
            name = " ".join(
                x for x in (getattr(ent, "first_name", None), getattr(ent, "last_name", None)) if x
            )
            gift.owner = name or None

    async def refresh(self) -> None:
        """Обновить список коллекций (появляются новые подарки)."""
        res = await self.client(functions.payments.GetStarGiftsRequest(hash=0))
        gifts = [g for g in (getattr(res, "gifts", None) or []) if getattr(g, "title", None)]
        upgradable = [g for g in gifts if getattr(g, "upgrade_stars", None)]
        chosen = upgradable or gifts  # если схема изменилась — берём все
        found = {g.title: getattr(g, "availability_total", None) for g in chosen}
        for name in self.extra:
            found.setdefault(name, None)
        new = [n for n in found if n not in self.totals]
        self.totals.update(found)
        log.info("Коллекций: %d (новых: %d)", len(self.totals), len(new))

    # ------------------------------------------------------------------ старт
    async def find_last(self, name: str, total: int | None) -> int:
        """Номер последнего существующего подарка (поиск делением пополам)."""
        if await self.fetch(name, 1) is None:
            return 0
        lo = 1
        if total:
            if total > 1 and await self.fetch(name, total) is not None:
                return total
            hi = total
        else:
            n = 2
            while n <= MAX_PROBE and await self.fetch(name, n) is not None:
                lo, n = n, n * 2
            hi = n
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if await self.fetch(name, mid) is not None:
                lo = mid
            else:
                hi = mid
        return lo

    async def _init_collection(self, name: str) -> None:
        for attempt in range(3):
            try:
                last = await self.find_last(name, self.totals.get(name))
                self.pos[name] = last
                self.db.set_pos(name, last)
                self.retry.discard(name)
                log.info("%s: стартовая позиция #%d", name, last)
                return
            except Exception:
                log.exception("Не удалось определить позицию %s (попытка %d)", name, attempt + 1)
                await asyncio.sleep(5)
        self.retry.add(name)

    async def seed(self) -> None:
        """Первый запуск: запоминаем текущие номера, чтобы не слать старые подарки."""
        seeded = self.db.get_meta("seeded") == "1"
        for name in list(self.totals):
            if name in self.pos:
                continue
            if seeded:
                # коллекция появилась уже после первого запуска — её улучшения идут с №1
                self.pos[name] = 0
                self.db.set_pos(name, 0)
            else:
                await self._init_collection(name)
        self._maybe_mark_seeded()

    def _maybe_mark_seeded(self) -> None:
        if not self.retry and all(n in self.pos for n in self.totals):
            self.db.set_meta("seeded", "1")

    async def catch_up(self, name: str) -> None:
        """Тихо перепрыгнуть к текущему последнему номеру коллекции (без уведомлений).

        Если ничего не улучшили, это один запрос. Иначе номер ищется «галопом»
        (шаги 1, 2, 4, …) и делением пополам, а не перебором каждого номера.
        """
        last = self.pos.get(name)
        if last is None:
            return
        total = self.totals.get(name)
        if total and last >= total:
            return
        if await self.fetch(name, last + 1) is None:
            return  # ничего нового
        lo, step, hi = last + 1, 1, None   # lo — точно существует
        while hi is None:
            if total and lo >= total:
                break
            probe = lo + step
            if total:
                probe = min(probe, total)
            if await self.fetch(name, probe) is not None:
                lo, step = probe, step * 2
            else:
                hi = probe
        while hi is not None and hi - lo > 1:
            mid = (lo + hi) // 2
            if await self.fetch(name, mid) is not None:
                lo = mid
            else:
                hi = mid
        self.pos[name] = lo
        self.db.set_pos(name, lo)
        log.info("%s: пропущено %d улучшений, пока бот был выключен (теперь #%d)", name, lo - last, lo)

    # ------------------------------------------------------------------ основной цикл
    async def poll_one(self, name: str) -> None:
        last = self.pos.get(name)
        if last is None:
            return
        total = self.totals.get(name)
        if total and last >= total:
            return  # все улучшения коллекции уже выданы
        while True:
            gift = await self.fetch(name, last + 1)
            if gift is None:
                break
            last += 1
            self.pos[name] = last
            self.db.set_pos(name, last)
            if gift.burned:
                log.info("Сожжён, пропускаю: %s #%d", name, last)
            else:
                await self._resolve_owner(gift)
                log.info(
                    "Новый: %s #%d (владелец: %s)",
                    name, last, gift.owner_username or gift.owner_id or gift.owner or "скрыт",
                )
                self.on_new(gift)
            if total and last >= total:
                break

    async def run(self) -> None:
        await self.refresh()
        await self.seed()
        log.info("Слежу за %d коллекциями", len(self.totals))
        last_refresh = time.monotonic()
        while True:
            for name in list(self.totals):
                try:
                    if name in self.need_catchup:
                        await self.catch_up(name)
                        self.need_catchup.discard(name)
                    await self.poll_one(name)
                except Exception:
                    # если догнать не вышло, коллекция остаётся в need_catchup и не опрашивается
                    # до успеха: так старые подарки не разошлются как новые
                    log.exception("Ошибка опроса %s", name)
            if time.monotonic() - last_refresh > self.refresh_s:
                try:
                    await self.refresh()
                    await self.seed()
                    for name in list(self.retry):
                        await self._init_collection(name)
                    self._maybe_mark_seeded()
                except Exception:
                    log.exception("Ошибка обновления списка коллекций")
                last_refresh = time.monotonic()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Наблюдатель упал, перезапуск через 15 с")
                await asyncio.sleep(15)
