import json
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from reposter_bot.relink import apply_changes, export_message, load_export, replacement

OLD = "https://t.me/+Ucj6avweaLNmMDNi"
NEW = "https://t.me/+tBVKx_7hKYIzMzgy"


def test_export_replaces_only_footer_link_preserving_emoji_offsets_and_hashtag() -> None:
    raw = {
        "type": "message",
        "id": 123,
        "text": "Текст 😀\n\nToy 🖤 | Предложка\n#предложка",
        "text_entities": [
            {"type": "bold", "text": "Текст 😀"},
            {"type": "plain", "text": "\n\n"},
            {"type": "text_link", "text": "Toy 🖤", "href": OLD},
            {"type": "plain", "text": " | "},
            {"type": "text_link", "text": "Предложка", "href": "https://t.me/toy_predlozhka_bot"},
            {"type": "plain", "text": "\n"},
            {"type": "hashtag", "text": "#предложка"},
        ],
    }
    message = export_message(raw, -100123)
    assert message is not None
    updated = replacement(message, OLD, NEW)
    assert updated is not None
    assert updated["text"] == raw["text"]
    assert updated["entities"][1] == {"type": "text_link", "offset": 10, "length": 6, "url": NEW}
    assert updated["entities"][2:] == message["entities"][2:]
    assert message["entities"][1]["url"] == OLD


def test_unrelated_link_is_not_changed() -> None:
    message = {
        "text": "Toy 🖤 ссылка в середине текста",
        "entities": [{"type": "text_link", "offset": 0, "length": 6, "url": OLD}],
    }
    assert replacement(message, OLD, NEW) is None


def test_media_caption_supports_legacy_join_label() -> None:
    raw = {
        "type": "message",
        "id": 55,
        "photo": "photos/photo.jpg",
        "text": "join",
        "text_entities": [{"type": "text_link", "text": "join", "href": OLD}],
    }
    message = export_message(raw, -100123)
    assert message is not None
    updated = replacement(message, OLD, NEW)
    assert updated is not None
    assert updated["caption"] == "join"
    assert updated["caption_entities"][0]["url"] == NEW


def test_export_from_wrong_channel_is_rejected(tmp_path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"type": "private_channel", "id": 999, "messages": []}))
    with pytest.raises(ValueError, match="different channel"):
        load_export(path, -100123)


@pytest.mark.asyncio
async def test_resume_only_edits_unfinished_posts(monkeypatch, tmp_path) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"updated": [1], "unchanged": [], "failed": []}))
    args = Namespace(resume=True, report=report, concurrency=3, chat_id=-100123)
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(type="channel")
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setattr("reposter_bot.relink.Bot", lambda token: bot)
    changes = [
        ({}, {"message_id": mid, "caption": "Toy 🖤", "caption_entities": []}) for mid in (1, 2)
    ]

    result = await apply_changes(changes, args)

    bot.edit_message_caption.assert_awaited_once()
    assert bot.edit_message_caption.await_args.kwargs["message_id"] == 2
    assert result == {"updated": [1, 2], "unchanged": [], "failed": []}
    assert json.loads(report.read_text()) == result
