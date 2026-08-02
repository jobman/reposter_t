from datetime import time

import pytest

from reposter_bot.config import parse_admin_ids, parse_bool, parse_clock


def test_parse_clock() -> None:
    assert parse_clock("19:05") == time(19, 5)


@pytest.mark.parametrize("value", ["25:00", "19", "aa:bb"])
def test_parse_clock_rejects_invalid_value(value: str) -> None:
    with pytest.raises(ValueError):
        parse_clock(value)


def test_parse_admin_ids() -> None:
    assert parse_admin_ids("123, 456") == frozenset({123, 456})


def test_parse_bool() -> None:
    assert parse_bool("yes", name="FLAG") is True
    assert parse_bool("OFF", name="FLAG") is False
