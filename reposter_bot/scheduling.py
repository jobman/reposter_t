from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class ScheduleConfig:
    start: time
    end: time
    count: int
    timezone: ZoneInfo

    def __post_init__(self) -> None:
        if not 1 <= self.count <= 100:
            raise ValueError("count must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class Slot:
    starts_at: datetime
    expires_at: datetime

    @property
    def key(self) -> str:
        return self.starts_at.isoformat()


def window_bounds(day: date, config: ScheduleConfig) -> tuple[datetime, datetime]:
    start = datetime.combine(day, config.start, tzinfo=config.timezone)
    end_day = day + timedelta(days=1) if config.end <= config.start else day
    end = datetime.combine(end_day, config.end, tzinfo=config.timezone)
    return start, end


def slots_for_day(day: date, config: ScheduleConfig) -> list[Slot]:
    start, end = window_bounds(day, config)
    interval = (end - start) / config.count
    starts = [start + interval * index for index in range(config.count)]
    return [
        Slot(starts_at=slot_start, expires_at=starts[index + 1] if index + 1 < len(starts) else end)
        for index, slot_start in enumerate(starts)
    ]


def active_slot(now: datetime, config: ScheduleConfig) -> Slot | None:
    local_now = now.astimezone(config.timezone)
    for day in (local_now.date(), local_now.date() - timedelta(days=1)):
        for slot in slots_for_day(day, config):
            if slot.starts_at <= local_now < slot.expires_at:
                return slot
    return None


def next_slot(now: datetime, config: ScheduleConfig) -> datetime:
    local_now = now.astimezone(config.timezone)
    candidates: list[datetime] = []
    for offset in (-1, 0, 1, 2):
        day = local_now.date() + timedelta(days=offset)
        candidates.extend(slot.starts_at for slot in slots_for_day(day, config))
    return min(candidate for candidate in candidates if candidate > local_now)
