from __future__ import annotations

import pytest

from src.features.game_state import parse_clock_seconds, regulation_seconds_remaining


@pytest.mark.parametrize(
    ("clock", "expected"),
    [("PT06M07.00S", 367.0), ("PT00M02.60S", 2.6), ("PT12M00.00S", 720.0)],
)
def test_parse_historical_clock(clock: str, expected: float) -> None:
    assert parse_clock_seconds(clock) == pytest.approx(expected)


@pytest.mark.parametrize("clock", ["6:07", "", "PT13M00.00S"])
def test_bad_clock_is_rejected(clock: str) -> None:
    with pytest.raises(ValueError):
        parse_clock_seconds(clock)


def test_regulation_clock_conversion_has_no_future_overtime_leakage() -> None:
    assert regulation_seconds_remaining(1, 720) == 2880
    assert regulation_seconds_remaining(4, 360) == 360
    assert regulation_seconds_remaining(5, 300) == 0
    assert regulation_seconds_remaining(7, 175) == 0

