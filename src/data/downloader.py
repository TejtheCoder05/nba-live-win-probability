"""Resumable bulk downloader for historical play-by-play.

Turns the Phase 1 single-game proof of concept into something that can be
pointed at whole seasons, interrupted, and restarted without losing work or
re-downloading what it already has.

Design priorities, in order:

1. **Correctness** — a game is only marked complete when its saved response has
   been validated. A partial download is worse than no download, because it is
   invisible.
2. **Resumability** — the process can die at any moment. State lives on disk
   (raw files + manifest), never only in memory.
3. **Politeness** — sequential requests with deliberate spacing. There is no
   concurrency here on purpose; stats.nba.com is an unofficial endpoint and
   getting throttled mid-season costs far more time than it saves.

Speed is explicitly last.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from src.data.game_log import (
    build_game_index,
    fetch_season_game_log,
    load_cached_game_log,
    save_game_log,
    unique_game_ids,
)
from src.data.manifest import ACTION_SKIPPED, DownloadManifest
from src.data.play_by_play import (
    existing_raw_path,
    fetch_play_by_play,
    save_raw_play_by_play,
)
from src.data.validation import ValidationResult, validate_cached_file, validate_play_by_play

logger = logging.getLogger(__name__)

# Slower than the Phase 1 default. At single-game scale spacing barely matters;
# across 1,230 sequential games it is the difference between a polite client and
# one that looks like an attack.
DEFAULT_BULK_SPACING = 1.0

# How often to emit a progress line, in games.
DEFAULT_PROGRESS_EVERY = 10


@dataclass
class DownloadStats:
    """Outcome of a download run over one season."""

    season: str
    season_type: str
    total: int = 0  # games in the season
    considered: int = 0  # games this run looked at (after --limit)
    downloaded: int = 0  # fetched from the API this run
    skipped: int = 0  # already valid on disk
    failed: int = 0
    events: int = 0  # play-by-play events downloaded this run
    elapsed_seconds: float = 0.0
    failed_game_ids: list[str] = field(default_factory=list)

    def as_line(self) -> str:
        return (
            f"[{self.season}] downloaded={self.downloaded} skipped={self.skipped} "
            f"failed={self.failed} events={self.events} "
            f"in {self.elapsed_seconds:.1f}s"
        )


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}"


def get_season_game_ids(
    season: str,
    season_type: str = "Regular Season",
    refresh: bool = False,
) -> tuple[list[str], pd.DataFrame]:
    """Return this season's deduplicated GAME_IDs and its one-row-per-game index.

    Reuses the Phase 1 game-log cache, so repeated runs over the same season
    cost zero API calls here.
    """
    game_log = None if refresh else load_cached_game_log(season, season_type)
    if game_log is None:
        game_log = fetch_season_game_log(season=season, season_type=season_type)
        save_game_log(game_log, season, season_type)

    game_ids = unique_game_ids(game_log)
    index = build_game_index(game_log)
    return game_ids, index


def is_game_complete(game_id: str) -> bool:
    """True when a valid, complete raw response for ``game_id`` is on disk.

    This deliberately validates rather than checking existence. A file left
    behind by an interrupted write exists but is not complete, and treating it
    as complete would silently corrupt the dataset.
    """
    return check_cached_game(game_id).is_valid


def check_cached_game(game_id: str) -> ValidationResult:
    """Validate a game's cached file, returning the full result.

    The result carries the event count, so a skipped game can report its true
    size rather than relying on the manifest. That keeps event totals correct
    even if the manifest is lost or rebuilt.
    """
    return validate_cached_file(existing_raw_path(game_id), game_id)


def download_game(game_id: str, save_csv: bool = False, spacing: float = DEFAULT_BULK_SPACING) -> int:
    """Download, validate, and persist one game. Returns the event count.

    Raises ``ValueError`` if the response fails validation, so an invalid
    response is never written to disk and mistaken for a cached game later.
    """
    events, raw = fetch_play_by_play(game_id, request_spacing=spacing)

    result = validate_play_by_play(raw, game_id)
    if not result.is_valid:
        raise ValueError(f"invalid response for {game_id}: {result.reason}")

    save_raw_play_by_play(game_id, events, raw, save_csv=save_csv)
    return result.event_count


def download_season(
    season: str,
    season_type: str = "Regular Season",
    limit: int | None = None,
    retry_failed: bool = True,
    save_csv: bool = False,
    spacing: float = DEFAULT_BULK_SPACING,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    refresh_game_log: bool = False,
    only_game_ids: list[str] | None = None,
    downloader: Callable[..., int] = download_game,
) -> DownloadStats:
    """Download every game in one season, skipping what is already complete.

    ``only_game_ids`` restricts the run to a specific subset (used by
    ``--only-failed``). ``downloader`` is injectable so tests can exercise the
    orchestration logic (skipping, resuming, failure recording) without making
    network calls.
    """
    started = time.monotonic()

    game_ids, _ = get_season_game_ids(season, season_type, refresh=refresh_game_log)
    manifest = DownloadManifest.load(season, season_type)
    new_games = manifest.register_games(game_ids)
    if new_games:
        logger.info("[%s] %d games registered in manifest", season, new_games)

    stats = DownloadStats(season=season, season_type=season_type, total=len(game_ids))

    targets = game_ids
    if only_game_ids is not None:
        wanted = set(only_game_ids)
        targets = [game_id for game_id in targets if game_id in wanted]
    if limit is not None:
        targets = targets[:limit]
    stats.considered = len(targets)

    logger.info(
        "[%s %s] %d games in season, %d targeted this run (limit=%s, retry_failed=%s)",
        season,
        season_type,
        stats.total,
        stats.considered,
        limit,
        retry_failed,
    )

    for position, game_id in enumerate(targets, start=1):
        entry = manifest.games.get(game_id, {})

        # Skip games already failed this cycle unless we were asked to retry.
        if not retry_failed and entry.get("status") == "failed":
            continue

        # Resume point: a valid file on disk means there is nothing to do. This
        # is checked against the filesystem, not just the manifest, so a deleted
        # raw file is correctly re-downloaded and a manifest lost to disk
        # corruption costs nothing.
        cached = check_cached_game(game_id)
        if cached.is_valid:
            stats.skipped += 1
            # Use the event count read from the file itself, not the manifest,
            # so totals stay correct even after a manifest loss.
            manifest.mark_downloaded(game_id, cached.event_count, action=ACTION_SKIPPED)
        else:
            try:
                event_count = downloader(game_id, save_csv=save_csv, spacing=spacing)
                stats.downloaded += 1
                stats.events += event_count
                manifest.mark_downloaded(game_id, event_count)
            except Exception as exc:  # noqa: BLE001 - one bad game must not stop the run
                stats.failed += 1
                stats.failed_game_ids.append(game_id)
                manifest.mark_failed(game_id, type(exc).__name__, str(exc))
                logger.warning("[%s] %s FAILED: %s: %s", season, game_id, type(exc).__name__, exc)

        # Persist progress as we go. Saving every game keeps the manifest
        # accurate to within one game if the process is killed.
        manifest.save()

        if position % progress_every == 0 or position == len(targets):
            elapsed = time.monotonic() - started
            rate = position / elapsed if elapsed > 0 else 0
            remaining = (len(targets) - position) / rate if rate > 0 else 0
            logger.info(
                "[%s] %d/%d | downloaded=%d skipped=%d failed=%d | %.2f games/s | ETA %s",
                season,
                position,
                len(targets),
                stats.downloaded,
                stats.skipped,
                stats.failed,
                rate,
                _format_duration(remaining),
            )

    stats.elapsed_seconds = time.monotonic() - started
    manifest.save()
    logger.info("[%s] done: %s", season, stats.as_line())
    return stats


def download_seasons(
    seasons: list[str],
    season_type: str = "Regular Season",
    **kwargs,
) -> list[DownloadStats]:
    """Download several seasons in sequence, one fully before the next starts."""
    results = []
    for season in seasons:
        results.append(download_season(season, season_type=season_type, **kwargs))
    return results
