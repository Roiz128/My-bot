from __future__ import annotations

import sqlite3

from models import UserFilter

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 1,
    collections TEXT NOT NULL DEFAULT '',
    max_num INTEGER,
    max_rarity INTEGER,
    backdrops TEXT NOT NULL DEFAULT '',
    only_original INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS positions (
    collection TEXT PRIMARY KEY,
    last_num INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_USER_FIELDS = {"active", "collections", "max_num", "max_rarity", "backdrops", "only_original"}


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Старые базы без колонки backdrops — добавляем её."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}
        if "backdrops" not in cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN backdrops TEXT NOT NULL DEFAULT ''")
        if "only_original" not in cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN only_original INTEGER NOT NULL DEFAULT 0")

    # ---------- users ----------
    @staticmethod
    def _to_filter(r: sqlite3.Row) -> UserFilter:
        cols = [c for c in r["collections"].split(",") if c.strip()]
        return UserFilter(
            chat_id=r["chat_id"],
            active=bool(r["active"]),
            collections=cols,
            max_num=r["max_num"],
            max_rarity=r["max_rarity"],
            backdrops=[b for b in r["backdrops"].split(",") if b.strip()],
            only_original=bool(r["only_original"]),
        )

    def subscribe(self, chat_id: int) -> UserFilter:
        self.conn.execute(
            "INSERT INTO users(chat_id, active) VALUES(?, 1) "
            "ON CONFLICT(chat_id) DO UPDATE SET active=1",
            (chat_id,),
        )
        self.conn.commit()
        return self.get_user(chat_id)  # type: ignore[return-value]

    def get_user(self, chat_id: int) -> UserFilter | None:
        r = self.conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        return self._to_filter(r) if r else None

    def update_user(self, chat_id: int, **fields) -> None:
        bad = set(fields) - _USER_FIELDS
        if bad:
            raise ValueError(f"unknown fields: {bad}")
        for key in ("collections", "backdrops"):
            if key in fields and isinstance(fields[key], list):
                fields[key] = ",".join(fields[key])
        for key in ("active", "only_original"):
            if key in fields:
                fields[key] = int(bool(fields[key]))
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE users SET {sets} WHERE chat_id=?", (*fields.values(), chat_id))
        self.conn.commit()

    def reset_filters(self, chat_id: int) -> None:
        self.conn.execute(
            "UPDATE users SET collections='', max_num=NULL, max_rarity=NULL, backdrops='', only_original=0 WHERE chat_id=?",
            (chat_id,),
        )
        self.conn.commit()

    def active_users(self) -> list[UserFilter]:
        rows = self.conn.execute("SELECT * FROM users WHERE active=1").fetchall()
        return [self._to_filter(r) for r in rows]

    # ---------- positions ----------
    def all_positions(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT collection, last_num FROM positions").fetchall()
        return {r["collection"]: r["last_num"] for r in rows}

    def set_pos(self, collection: str, num: int) -> None:
        self.conn.execute(
            "INSERT INTO positions(collection, last_num) VALUES(?, ?) "
            "ON CONFLICT(collection) DO UPDATE SET last_num=excluded.last_num",
            (collection, num),
        )
        self.conn.commit()

    # ---------- meta ----------
    def get_meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()
