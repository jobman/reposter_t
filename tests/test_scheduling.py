from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from reposter_bot.scheduling import (
    ScheduleConfig,
    active_slot,
    next_slot,
    slots_for_day,
)

KYIV = ZoneInfo("Europe/Kyiv")
CONFIG = ScheduleConfig(start=time(19), end=time(2), count=10, timezone=KYIV)


def test_even_slots_cross_midnight() -> None:
    slots = slots_for_day(date(2026, 8, 2), CONFIG)
    assert len(slots) == 10
    assert slots[0].starts_at.strftime("%Y-%m-%d %H:%M") == "2026-08-02 19:00"
    assert slots[1].starts_at.strftime("%Y-%m-%d %H:%M") == "2026-08-02 19:42"
    assert slots[-1].starts_at.strftime("%Y-%m-%d %H:%M") == "2026-08-03 01:18"
    assert slots[-1].expires_at.strftime("%Y-%m-%d %H:%M") == "2026-08-03 02:00"


def test_active_slot_during_overnight_window() -> None:
    now = datetime(2026, 8, 2, 22, 5, tzinfo=KYIV).astimezone(UTC)
    slot = active_slot(now, CONFIG)
    assert slot is not None
    assert slot.starts_at.strftime("%H:%M") == "21:48"


def test_no_active_slot_during_day() -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=KYIV).astimezone(UTC)
    assert active_slot(now, CONFIG) is None


def test_next_slot_before_window() -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=KYIV).astimezone(UTC)
    assert next_slot(now, CONFIG).strftime("%Y-%m-%d %H:%M") == "2026-08-02 19:00"
