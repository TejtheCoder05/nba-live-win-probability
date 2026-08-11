"""Validate raw ``PlayByPlayV3`` responses before treating them as cached.

The downloader is resumable, which means "this file exists" gets interpreted as
"this game is done". That inference is only safe if the file is known to be
*complete and correct*. A run interrupted mid-write, a response truncated by a
dropped connection, or an error page returned with a 200 status would all
otherwise be silently baked into the dataset and only surface much later as
inexplicable gaps in training data.

So every response is validated twice: once when downloaded, and again whenever a
cached file is considered for skipping.

The checks here are deliberately about **structural usability**, mirroring what
the Phase 1 schema test proved we need: the right game, a non-empty event
stream, and the presence of period, clock, and action information.
"""

from __future__ import annotations

import gzip
import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.data.play_by_play import read_raw_json

# Matches the ISO-8601 duration the endpoint uses for the game clock,
# e.g. "PT12M00.00S". Confirmed against real responses in Phase 1.
CLOCK_PATTERN = re.compile(r"^PT(\d+)M(\d+(?:\.\d+)?)S$")

# Fields every action must carry for the Phase 3 feature pipeline to work.
REQUIRED_ACTION_FIELDS = ("period", "clock", "actionType")

# A real NBA game has hundreds of events. Anything below this is a truncated or
# in-progress response rather than a completed game.
MIN_EXPECTED_EVENTS = 50

# Fraction of events whose clock must parse for the response to be usable.
MIN_PARSEABLE_CLOCK_RATIO = 0.95


@dataclass
class ValidationResult:
    """Outcome of validating one play-by-play response."""

    game_id: str
    is_valid: bool
    event_count: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        """Single-line summary, suitable for a log line or manifest entry."""
        return "; ".join(self.problems) if self.problems else "ok"


def validate_play_by_play(raw: Any, expected_game_id: str) -> ValidationResult:
    """Check that ``raw`` is a complete play-by-play response for the right game.

    Returns a :class:`ValidationResult` rather than raising, because the caller
    wants to record *why* a game failed, not just that it did.
    """
    problems: list[str] = []

    # --- Shape -------------------------------------------------------------
    if not isinstance(raw, dict):
        return ValidationResult(expected_game_id, False, 0, [f"response is {type(raw).__name__}, not a dict"])

    game = raw.get("game")
    if not isinstance(game, dict):
        return ValidationResult(expected_game_id, False, 0, ["response has no 'game' object"])

    # --- Identity: did we get back the game we asked for? ------------------
    returned_id = str(game.get("gameId", ""))
    if returned_id != str(expected_game_id):
        problems.append(f"gameId mismatch: requested {expected_game_id}, got {returned_id or 'nothing'}")

    # --- Event stream ------------------------------------------------------
    actions = game.get("actions")
    if not isinstance(actions, list):
        problems.append("'actions' is missing or not a list")
        return ValidationResult(expected_game_id, False, 0, problems)

    event_count = len(actions)
    if event_count == 0:
        problems.append("play-by-play is empty")
        return ValidationResult(expected_game_id, False, 0, problems)

    if event_count < MIN_EXPECTED_EVENTS:
        problems.append(f"only {event_count} events (expected >= {MIN_EXPECTED_EVENTS}); likely truncated")

    # --- Required fields present on every action ---------------------------
    for name in REQUIRED_ACTION_FIELDS:
        missing = sum(1 for action in actions if name not in action)
        if missing:
            problems.append(f"'{name}' missing from {missing}/{event_count} events")

    # --- Period information is usable --------------------------------------
    periods = [action.get("period") for action in actions if isinstance(action.get("period"), int)]
    if not periods:
        problems.append("no usable integer 'period' values")
    elif min(periods) < 1:
        problems.append(f"invalid period value {min(periods)}")

    # --- Clock information is usable ---------------------------------------
    clocks = [str(action.get("clock", "")) for action in actions]
    parseable = sum(1 for clock in clocks if CLOCK_PATTERN.match(clock.strip()))
    if parseable == 0:
        problems.append("no parseable clock values")
    elif parseable / event_count < MIN_PARSEABLE_CLOCK_RATIO:
        problems.append(f"only {parseable}/{event_count} clock values parse")

    # --- Action/event information is usable --------------------------------
    # Blank actionType is legitimate on BLOCK/STEAL credit rows (see
    # docs/FIELD_MAP.md), so we require a strong majority, not all.
    labelled = sum(1 for action in actions if str(action.get("actionType", "")).strip())
    if labelled == 0:
        problems.append("no events carry an actionType")
    elif labelled / event_count < 0.5:
        problems.append(f"only {labelled}/{event_count} events have an actionType")

    return ValidationResult(
        game_id=expected_game_id,
        is_valid=not problems,
        event_count=event_count,
        problems=problems,
    )


def validate_cached_file(path: Path | None, expected_game_id: str) -> ValidationResult:
    """Validate a play-by-play file already on disk (gzipped or plain).

    Unreadable, corrupt, or malformed files are reported as validation failures
    rather than raised, so a single bad file cannot abort a long download run.
    This is what stops a half-written file from being mistaken for a completed
    game. ``path`` may be ``None``, meaning nothing was found on disk.
    """
    if path is None or not path.exists():
        return ValidationResult(expected_game_id, False, 0, ["file does not exist"])

    try:
        raw = read_raw_json(path)
    except json.JSONDecodeError as exc:
        return ValidationResult(expected_game_id, False, 0, [f"corrupt JSON: {exc}"])
    except UnicodeDecodeError as exc:
        return ValidationResult(expected_game_id, False, 0, [f"undecodable file: {exc}"])
    # zlib.error is NOT an OSError subclass, so a truncated .gz would otherwise
    # escape and abort an entire download run.
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
        return ValidationResult(expected_game_id, False, 0, [f"unreadable file: {exc}"])

    return validate_play_by_play(raw, expected_game_id)
