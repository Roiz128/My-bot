"""Общие структуры данных и фильтры (без зависимостей от Telegram-библиотек)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def norm(text: str) -> str:
    """'Durov's Cap' -> 'durovscap' — для сравнения названий коллекций."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def slugify(title: str) -> str:
    """Название коллекции -> часть slug в ссылке t.me/nft/<Slug>-<N>."""
    return re.sub(r"[^A-Za-z0-9]", "", title)


@dataclass
class GiftInfo:
    collection: str
    num: int
    slug: str
    issued: int | None = None
    total: int | None = None
    model: str | None = None
    model_permille: int | None = None
    backdrop: str | None = None
    backdrop_permille: int | None = None
    pattern: str | None = None
    pattern_permille: int | None = None
    owner: str | None = None
    owner_id: int | None = None
    owner_username: str | None = None
    burned: bool = False  # подарок существует, но сожжён (не рассылаем)
    has_original: bool = False  # есть подпись «Подарок от … для …»

    @property
    def url(self) -> str:
        return f"https://t.me/nft/{self.slug}"


@dataclass
class UserFilter:
    chat_id: int
    active: bool = True
    collections: list[str] = field(default_factory=list)  # пусто = все
    max_num: int | None = None          # номер подарка <= max_num
    max_rarity: int | None = None       # редкость модели <= X промилле (10 = 1%)
    backdrops: list[str] = field(default_factory=list)  # пусто = любой фон
    only_original: bool = False  # только подарки с подписью «от кого / кому»


def matches(f: UserFilter, g: GiftInfo) -> bool:
    if f.only_original and not g.has_original:
        return False
    if f.collections:
        wanted = {norm(c) for c in f.collections}
        if norm(g.collection) not in wanted:
            return False
    if f.max_num is not None and g.num > f.max_num:
        return False
    if f.max_rarity is not None:
        if g.model_permille is None or g.model_permille > f.max_rarity:
            return False
    if f.backdrops:
        if not g.backdrop:
            return False
        bd = norm(g.backdrop)
        # 'black' подходит и к 'Black', и к 'Onyx Black'
        if not any(norm(w) and norm(w) in bd for w in f.backdrops):
            return False
    return True
