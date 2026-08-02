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
    suggestion_bot_token: str
    suggestion_admin_id: int
    posting_enabled: bool
    target_chat_id: int | None
    join_url: str
    link_text: str
    suggestion_bot_url: str
    suggestion_link_text: str
    suggestion_media_path: Path
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
        link_text = os.getenv("LINK_TEXT", "Toy 🖤").strip()
        if not link_text:
            raise ValueError("LINK_TEXT must not be empty")
        suggestion_bot_url = os.getenv(
            "SUGGESTION_BOT_URL", "https://t.me/toy_predlozhka_bot"
        ).strip()
        if not suggestion_bot_url.startswith(("https://t.me/", "http://t.me/")):
            raise ValueError("SUGGESTION_BOT_URL must be a Telegram HTTP(S) URL")
        suggestion_link_text = os.getenv("SUGGESTION_LINK_TEXT", "Предложка").strip()
        if not suggestion_link_text:
            raise ValueError("SUGGESTION_LINK_TEXT must not be empty")

        return cls(
            bot_token=token,
            setup_secret=os.getenv("SETUP_SECRET", "").strip(),
            suggestion_bot_token=os.getenv("SUGGESTION_BOT_TOKEN", "").strip(),
            suggestion_admin_id=int(os.getenv("SUGGESTION_ADMIN_ID", "192884752")),
            posting_enabled=parse_bool(
                os.getenv("POSTING_ENABLED", "false"), name="POSTING_ENABLED"
            ),
            target_chat_id=target_chat_id,
            join_url=join_url,
            link_text=link_text,
            suggestion_bot_url=suggestion_bot_url,
            suggestion_link_text=suggestion_link_text,
            suggestion_media_path=Path(os.getenv("SUGGESTION_MEDIA_PATH", "data/suggestions")),
            timezone=timezone,
            window_start=parse_clock(os.getenv("WINDOW_START", "19:00")),
            window_end=parse_clock(os.getenv("WINDOW_END", "02:00")),
            posts_per_window=posts_per_window,
            database_path=Path(os.getenv("DATABASE_PATH", "data/reposter.sqlite3")),
            admin_user_ids=parse_admin_ids(os.getenv("ADMIN_USER_IDS", "")),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )
