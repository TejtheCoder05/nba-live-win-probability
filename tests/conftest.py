"""Shared pytest fixtures.

The schema tests run against two sources of play-by-play:

* ``fixture``    - a small, committed slice of a real response. Always available,
                   so the suite passes on a fresh clone with no network access.
* ``downloaded`` - a full game under ``data/raw/`` if one has been downloaded.
                   Skipped when absent, since ``data/raw/`` is gitignored.

Running the same assertions against both means the committed fixture cannot
quietly drift away from what the real API returns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.data.play_by_play import events_from_raw
from src.paths import PLAY_BY_PLAY_DIR, TESTS_FIXTURE_DIR

FIXTURE_PATH = TESTS_FIXTURE_DIR / "playbyplay_sample.json"


def _downloaded_game_files() -> list[Path]:
    """Any full games downloaded into data/raw/playbyplay/."""
    if not PLAY_BY_PLAY_DIR.exists():
        return []
    return sorted(PLAY_BY_PLAY_DIR.glob("*.json"))


def _load_events(path: Path) -> pd.DataFrame:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return events_from_raw(raw)


@pytest.fixture(scope="session")
def fixture_events() -> pd.DataFrame:
    """Events from the committed sample fixture."""
    assert FIXTURE_PATH.exists(), (
        f"Missing test fixture {FIXTURE_PATH}. "
        "Regenerate it with: python scripts/make_test_fixture.py <GAME_ID>"
    )
    return _load_events(FIXTURE_PATH)


@pytest.fixture(params=["fixture", "downloaded"])
def events(request: pytest.FixtureRequest) -> pd.DataFrame:
    """Play-by-play events from each available source."""
    if request.param == "fixture":
        return _load_events(FIXTURE_PATH)

    downloaded = _downloaded_game_files()
    if not downloaded:
        pytest.skip("No downloaded games in data/raw/playbyplay/ (run scripts/phase1_poc.py)")
    return _load_events(downloaded[0])
