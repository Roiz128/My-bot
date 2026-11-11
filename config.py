from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    session: str
    db_path: str
    rps: float                 # запросов к Telegram в секунду
    refresh_minutes: int       # как часто обновлять список коллекций
    extra_collections: list[str]


def load_config() -> Config:
    _load_dotenv()

    def need(name: str) -> str:
        v = os.environ.get(name, "").strip()
        if not v:
            raise SystemExit(f"Не задана переменная {name} (см. .env.example)")
        return v

    extra = [x.strip() for x in os.environ.get("EXTRA_COLLECTIONS", "").split(",") if x.strip()]
    return Config(
        api_id=int(need("API_ID")),
        api_hash=need("API_HASH"),
        bot_token=need("BOT_TOKEN"),
        session=os.environ.get("SESSION_NAME", "watcher"),
        db_path=os.environ.get("DB_PATH", "gifts.db"),
        rps=float(os.environ.get("REQUESTS_PER_SECOND", "8")),
        refresh_minutes=int(os.environ.get("REFRESH_MINUTES", "60")),
        extra_collections=extra,
    )
