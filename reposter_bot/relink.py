"""Replace a footer link using recorded messages or a Telegram Desktop JSON export."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import LinkPreviewOptions

FOOTER = re.compile(r"(?:Toy 🖤|join)(?: \| Предложка)?(?:\n#предложка)?\s*\Z")
EXPORT_ENTITY_TYPES = {
    "bold",
    "italic",
    "underline",
    "strikethrough",
    "spoiler",
    "code",
    "pre",
    "text_link",
    "url",
    "email",
    "mention",
    "hashtag",
    "cashtag",
    "bot_command",
    "phone_number",
    "custom_emoji",
    "blockquote",
    "expandable_blockquote",
}


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def slice_utf16(text: str, offset: int, length: int | None = None) -> str:
    data = text.encode("utf-16-le")
    end = None if length is None else (offset + length) * 2
    return data[offset * 2 : end].decode("utf-16-le")


def export_message(message: dict, chat_id: int) -> dict | None:
    if message.get("type") != "message":
        return None
    parts = message.get("text_entities")
    if not isinstance(parts, list):
        return None
    text = ""
    entities = []
    for part in parts:
        value = part.get("text", "")
        kind = part.get("type", "plain")
        offset = utf16_length(text)
        text += value
        if kind == "plain":
            continue
        if kind == "link":
            kind = "url"
        if kind not in EXPORT_ENTITY_TYPES:
            return None
        entity = {"type": kind, "offset": offset, "length": utf16_length(value)}
        if kind == "text_link":
            if not part.get("href"):
                return None
            entity["url"] = part["href"]
        if kind == "pre" and part.get("language"):
            entity["language"] = part["language"]
        if kind == "custom_emoji":
            emoji_id = part.get("document_id") or part.get("custom_emoji_id")
            if not emoji_id:
                return None
            entity["custom_emoji_id"] = str(emoji_id)
        entities.append(entity)
    raw_text = message.get("text", "")
    expected = (
        raw_text
        if isinstance(raw_text, str)
        else "".join(part if isinstance(part, str) else part.get("text", "") for part in raw_text)
    )
    if expected != text:
        return None
    has_media = bool(message.get("photo") or message.get("file") or message.get("media_type"))
    field = "caption" if has_media else "text"
    return {
        "chat_id": chat_id,
        "message_id": int(message["id"]),
        field: text,
        f"{field}_entities" if has_media else "entities": entities,
    }


def replacement(message: dict, old_url: str, new_url: str) -> dict | None:
    field = "caption" if "caption" in message else "text"
    text = message.get(field, "")
    entities_key = "caption_entities" if field == "caption" else "entities"
    entities = message.get(entities_key, [])
    changed = False
    updated = []
    for original in entities:
        entity = dict(original)
        if entity.get("type") == "text_link" and entity.get("url") == old_url:
            label = slice_utf16(text, entity["offset"], entity["length"])
            tail = slice_utf16(text, entity["offset"])
            if label in {"Toy 🖤", "join"} and FOOTER.fullmatch(tail):
                entity["url"] = new_url
                changed = True
        updated.append(entity)
    if not changed:
        return None
    result = dict(message)
    result[entities_key] = updated
    return result


def load_export(path: Path, chat_id: int) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if document.get("type") not in {"private_channel", "public_channel"}:
        raise ValueError("Expected a single channel export in JSON format")
    exported_id = int(document["id"])
    expected_id = exported_id if exported_id < 0 else -int(f"100{exported_id}")
    if expected_id != chat_id:
        raise ValueError("Export belongs to a different channel")
    messages = []
    for raw in document.get("messages", []):
        converted = export_message(raw, chat_id)
        if converted is not None:
            messages.append(converted)
    return messages


def load_archive(path: Path, chat_id: int) -> list[dict]:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT snapshot_json FROM channel_messages WHERE chat_id = ? ORDER BY message_id",
            (chat_id,),
        ).fetchall()
    return [dict(json.loads(row[0]), chat_id=chat_id) for row in rows]


def load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        os.environ[key.strip()] = value.strip()


async def apply_changes(changes: list[tuple[dict, dict]], args: argparse.Namespace) -> dict:
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        raise ValueError("BOT_TOKEN is required for --apply")
    bot = Bot(token)
    result = {"updated": [], "unchanged": [], "failed": []}
    try:
        chat = await bot.get_chat(args.chat_id)
        if chat.type != "channel":
            raise ValueError("Target chat is not a channel")
        for _, updated in changes:
            message_id = updated["message_id"]
            for attempt in range(4):
                try:
                    if "caption" in updated:
                        await bot.edit_message_caption(
                            chat_id=args.chat_id,
                            message_id=message_id,
                            caption=updated["caption"],
                            caption_entities=updated["caption_entities"],
                            parse_mode=None,
                            show_caption_above_media=updated.get("show_caption_above_media"),
                        )
                    else:
                        await bot.edit_message_text(
                            chat_id=args.chat_id,
                            message_id=message_id,
                            text=updated["text"],
                            entities=updated["entities"],
                            parse_mode=None,
                            link_preview_options=updated.get("link_preview_options")
                            or LinkPreviewOptions(is_disabled=True),
                        )
                    result["updated"].append(message_id)
                    break
                except TelegramRetryAfter as exc:
                    if attempt == 3:
                        result["failed"].append({"id": message_id, "error": "Rate limit"})
                    else:
                        await asyncio.sleep(exc.retry_after + 1)
                except TelegramBadRequest as exc:
                    if "message is not modified" in exc.message.lower():
                        result["unchanged"].append(message_id)
                    else:
                        result["failed"].append({"id": message_id, "error": exc.message})
                    break
            args.report.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await asyncio.sleep(0.5)
    finally:
        await bot.session.close()
    return result


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--export", type=Path)
    source.add_argument("--archive", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--chat-id", type=int, required=True)
    parser.add_argument("--old-url", required=True)
    parser.add_argument("--new-url", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("link-update-report.json"))
    args = parser.parse_args()
    for url in (args.old_url, args.new_url):
        if urlparse(url).scheme != "https" or urlparse(url).netloc != "t.me":
            parser.error("Only HTTPS t.me URLs are supported")
    if args.env_file:
        load_env(args.env_file)
    messages = (
        load_export(args.export, args.chat_id)
        if args.export
        else load_archive(args.archive, args.chat_id)
    )
    changes = []
    for message in messages:
        updated = replacement(message, args.old_url, args.new_url)
        if updated:
            changes.append((message, updated))
    print(json.dumps({"scanned": len(messages), "matching": len(changes), "apply": args.apply}))
    if not args.apply:
        return
    backup = args.report.with_suffix(".backup.json")
    if backup.exists():
        raise ValueError("Backup already exists; choose a new --report path")
    backup.write_text(
        json.dumps(
            {"created_at": datetime.now(UTC).isoformat(), "changes": changes},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    backup.chmod(0o600)
    result = asyncio.run(apply_changes(changes, args))
    print(json.dumps({key: len(value) for key, value in result.items()}))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
