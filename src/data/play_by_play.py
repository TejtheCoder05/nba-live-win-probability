"""Retrieve and persist historical play-by-play via ``PlayByPlayV3``.

One game's play-by-play is the raw material for every training row we will
eventually build: each event carries a clock, a period, a running score, an
action type, and the team responsible. This module fetches that stream and
stores it; it deliberately does **not** derive any features. Feature logic lives
in ``src/features`` (Phase 3) so that historical training and live inference can
share exactly one implementation.

Two files are written per game:

* ``{GAME_ID}.json`` — the untouched API response. This is the source of truth.
  If we later discover we need a field we did not flatten, we can reprocess from
  here rather than re-downloading thousands of games.
* ``{GAME_ID}.csv`` — the flattened event table, for quick human inspection.
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import PlayByPlayV3

from src.data.nba_client import call_endpoint
from src.paths import PLAY_BY_PLAY_DIR, ensure_dir

logger = logging.getLogger(__name__)

# PlayByPlayV3 requires a period range. 1..14 covers regulation plus a very
# generous allowance for overtime (the NBA record is 6 OT periods).
DEFAULT_START_PERIOD = 1
DEFAULT_END_PERIOD = 14


def fetch_play_by_play(game_id: str, request_spacing: float | None = None) -> tuple[pd.DataFrame, dict]:
    """Fetch one game's play-by-play.

    Returns ``(events_dataframe, raw_response_dict)``.

    ``request_spacing`` overrides the minimum gap between requests; the bulk
    downloader raises it, since spacing that is fine for one game is not
    necessarily fine for twelve hundred in a row.

    Note the named ``.play_by_play`` accessor. ``PlayByPlayV3`` returns *two*
    data sets and ``AvailableVideo`` comes first, so ``get_data_frames()[0]``
    would hand back a one-row video-availability table instead of the events.
    """
    logger.info("Fetching play-by-play for game %s", game_id)
    spacing_kwargs = {} if request_spacing is None else {"request_spacing": request_spacing}
    endpoint = call_endpoint(
        PlayByPlayV3,
        game_id=game_id,
        start_period=DEFAULT_START_PERIOD,
        end_period=DEFAULT_END_PERIOD,
        **spacing_kwargs,
    )
    events = endpoint.play_by_play.get_data_frame()
    raw = endpoint.get_dict()
    return events, raw


# Raw responses are gzipped by default. Play-by-play JSON is extremely
# repetitive, so it compresses ~23x: a full season is ~17 MB instead of ~400 MB,
# and three seasons are ~52 MB instead of ~1.2 GB. The content stored is still
# the untouched API response, just compressed on disk.
COMPRESS_BY_DEFAULT = True
COMPRESSED_SUFFIX = ".json.gz"
PLAIN_SUFFIX = ".json"


def play_by_play_paths(game_id: str, compress: bool = COMPRESS_BY_DEFAULT) -> tuple[Path, Path]:
    """Return the ``(raw_json_path, csv_path)`` for a game's play-by-play."""
    suffix = COMPRESSED_SUFFIX if compress else PLAIN_SUFFIX
    return (
        PLAY_BY_PLAY_DIR / f"{game_id}{suffix}",
        PLAY_BY_PLAY_DIR / f"{game_id}.csv",
    )


def existing_raw_path(game_id: str) -> Path | None:
    """Find a game's raw file on disk in either format, or ``None``.

    Compressed files win when both exist. Checking both formats is what lets
    already-downloaded plain-JSON games (from Phase 1, before compression was
    introduced) still count as complete instead of being re-downloaded.
    """
    for candidate in (
        PLAY_BY_PLAY_DIR / f"{game_id}{COMPRESSED_SUFFIX}",
        PLAY_BY_PLAY_DIR / f"{game_id}{PLAIN_SUFFIX}",
    ):
        if candidate.exists():
            return candidate
    return None


def read_raw_json(path: Path) -> dict:
    """Read a raw response, transparently handling gzip or plain JSON."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def write_raw_json(path: Path, raw: dict) -> None:
    """Write a raw response atomically, compressing when the path ends in .gz.

    The temp-file-then-rename is essential: a process killed mid-write would
    otherwise leave a truncated file that the resumable downloader could mistake
    for a completed game.
    """
    tmp = path.with_name(path.name + ".tmp")
    if path.suffix == ".gz":
        with gzip.open(tmp, "wt", encoding="utf-8") as handle:
            json.dump(raw, handle, separators=(",", ":"))
    else:
        tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_raw_play_by_play(
    game_id: str,
    events: pd.DataFrame,
    raw: dict,
    save_csv: bool = True,
    compress: bool = COMPRESS_BY_DEFAULT,
) -> tuple[Path, Path | None]:
    """Write the raw response, and optionally the flattened CSV.

    ``save_csv`` is off for bulk downloads. The CSV is a convenience for reading
    a single game by eye; at full-season scale it roughly doubles the file count
    for data we can regenerate from the JSON at any time.
    """
    ensure_dir(PLAY_BY_PLAY_DIR)
    json_path, csv_path = play_by_play_paths(game_id, compress=compress)

    write_raw_json(json_path, raw)

    if save_csv:
        events.to_csv(csv_path, index=False)
    else:
        csv_path = None

    logger.debug("Saved raw play-by-play for %s", game_id)
    return json_path, csv_path


def is_game_downloaded(game_id: str) -> bool:
    """True when a raw file for the game exists in either format.

    Existence only. The bulk downloader uses the stricter
    ``downloader.is_game_complete``, which also validates the contents.
    """
    return existing_raw_path(game_id) is not None


def load_raw_play_by_play(game_id: str) -> pd.DataFrame:
    """Rebuild the events DataFrame from a saved raw response.

    Reading back from the raw JSON (rather than the CSV) proves the stored
    response really is a sufficient source of truth, and is how the test suite
    validates saved data.
    """
    path = existing_raw_path(game_id)
    if path is None:
        raise FileNotFoundError(f"No raw play-by-play saved for game {game_id}")
    return events_from_raw(read_raw_json(path))


def events_from_raw(raw: dict) -> pd.DataFrame:
    """Convert a raw ``PlayByPlayV3`` response dict into the events DataFrame.

    The V3 response nests the events under ``game.actions`` as a list of
    objects, which is different from the older ``resultSets`` row/header layout
    used by V2-era endpoints.

    ``gameId`` is stored once at the game level rather than repeated on every
    action, so it is re-attached here. That makes this function's output match
    the endpoint's own DataFrame column-for-column, which means downstream code
    behaves identically whether it read from the API or from disk.
    """
    game = raw["game"]
    events = pd.DataFrame(game["actions"])
    events.insert(0, "gameId", game["gameId"])
    return events
