from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def parse_clock(value: str) -> time:
    try:
        hour_text, minute_text = value.strip().split(":", maxsplit=1)
        return time(hour=int(hour_text), minute=int(minute_text))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid time {value!r}; expected HH:MM") from exc


def parse_admin_ids(value: str) -> frozenset[int]:
    if not value.strip():
        return frozenset()
    try:
        return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError("ADMIN_USER_IDS must contain comma-separated integers") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    setup_secret: str
    posting_enabled: bool
    target_chat_id: int | None
    join_url: str
    timezone: ZoneInfo
    window_start: time
    window_end: time
    posts_per_window: int
    database_path: Path
    admin_user_ids: frozenset[int]
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("BOT_TOKEN is required")

        timezone_name = os.getenv("TIMEZONE", "Europe/Kyiv").strip()
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown TIMEZONE: {timezone_name}") from exc

        target_raw = os.getenv("TARGET_CHAT_ID", "").strip()
        target_chat_id = int(target_raw) if target_raw else None
        posts_per_window = int(os.getenv("POSTS_PER_WINDOW", "10"))
        if not 1 <= posts_per_window <= 100:
            raise ValueError("POSTS_PER_WINDOW must be between 1 and 100")

        join_url = os.getenv("JOIN_URL", "https://t.me/+Ucj6avweaLNmMDNi").strip()
        if not join_url.startswith(("https://t.me/", "http://t.me/")):
            raise ValueError("JOIN_URL must be a Telegram HTTP(S) URL")

        return cls(
            bot_token=token,
            setup_secret=os.getenv("SETUP_SECRET", "").strip(),
            posting_enabled=parse_bool(
                os.getenv("POSTING_ENABLED", "false"), name="POSTING_ENABLED"
            ),
            target_chat_id=target_chat_id,
            join_url=join_url,
            timezone=timezone,
            window_start=parse_clock(os.getenv("WINDOW_START", "19:00")),
            window_end=parse_clock(os.getenv("WINDOW_END", "02:00")),
            posts_per_window=posts_per_window,
            database_path=Path(os.getenv("DATABASE_PATH", "data/reposter.sqlite3")),
            admin_user_ids=parse_admin_ids(os.getenv("ADMIN_USER_IDS", "")),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )
