"""Machine-readable download status tracking.

One manifest per season records the state of every GAME_ID, so the answer to
"what still needs downloading?" is a file read rather than a re-scan of the API.

Two files are maintained:

* ``data/raw/manifest/{season}_{type}.json`` — current state of each game.
  Rewritten atomically as the download progresses.
* ``data/raw/manifest/failures.jsonl`` — append-only history of every failure
  across all runs and seasons. The manifest holds the *latest* state of a game;
  this holds the full audit trail, including failures that were later resolved.

Statuses
--------
``pending``     known to exist in the season, not yet successfully downloaded
``downloaded``  raw JSON on disk and validated
``failed``      attempted and failed; the entry records why

"Skipped/cached" is not a stored status but a per-run *action*: a game that was
already ``downloaded`` before this run started. Each entry records
``last_action`` so that distinction is still recoverable from the file.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.paths import MANIFEST_DIR, ensure_dir

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_DOWNLOADED = "downloaded"
STATUS_FAILED = "failed"

ACTION_DOWNLOADED = "downloaded"
ACTION_SKIPPED = "skipped"
ACTION_FAILED = "failed"

FAILURE_LOG_NAME = "failures.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _season_slug(season: str, season_type: str) -> str:
    return f"{season}_{season_type.lower().replace(' ', '_')}"


def manifest_path(season: str, season_type: str) -> Path:
    return MANIFEST_DIR / f"{_season_slug(season, season_type)}.json"


def failure_log_path() -> Path:
    return MANIFEST_DIR / FAILURE_LOG_NAME


@dataclass
class DownloadManifest:
    """The download state of every game in one season."""

    season: str
    season_type: str
    games: dict[str, dict[str, Any]] = field(default_factory=dict)

    # --- Construction ------------------------------------------------------

    @classmethod
    def load(cls, season: str, season_type: str) -> "DownloadManifest":
        """Load an existing manifest, or return an empty one.

        A corrupt manifest is not fatal: the raw JSON files on disk are the real
        source of truth, so we start a fresh manifest and let validation
        re-discover what is already downloaded.
        """
        path = manifest_path(season, season_type)
        if not path.exists():
            return cls(season=season, season_type=season_type)

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Manifest %s unreadable (%s); starting a new one", path.name, exc)
            return cls(season=season, season_type=season_type)

        return cls(
            season=payload.get("season", season),
            season_type=payload.get("season_type", season_type),
            games=payload.get("games", {}),
        )

    def save(self) -> Path:
        """Write the manifest atomically.

        Writing to a temporary file and renaming means an interrupted save can
        never leave a half-written manifest behind — the very failure mode the
        manifest exists to protect against.
        """
        ensure_dir(MANIFEST_DIR)
        path = manifest_path(self.season, self.season_type)
        payload = {
            "season": self.season,
            "season_type": self.season_type,
            "updated_at": _now(),
            "summary": self.summary(),
            "games": self.games,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    # --- Updating ----------------------------------------------------------

    def register_games(self, game_ids: Iterable[str]) -> int:
        """Add any not-yet-known games as ``pending``. Returns how many are new."""
        added = 0
        for game_id in game_ids:
            if game_id not in self.games:
                self.games[game_id] = {"status": STATUS_PENDING, "attempts": 0}
                added += 1
        return added

    def mark_downloaded(self, game_id: str, event_count: int, action: str = ACTION_DOWNLOADED) -> None:
        entry = self.games.setdefault(game_id, {"attempts": 0})
        entry.update(
            {
                "status": STATUS_DOWNLOADED,
                "last_action": action,
                "event_count": event_count,
                "updated_at": _now(),
            }
        )
        # A previously failed game that now succeeds should not keep its error.
        entry.pop("error", None)
        entry.pop("error_type", None)

    def mark_failed(self, game_id: str, error_type: str, error: str) -> None:
        """Record a failure in the manifest and append it to the failure log."""
        entry = self.games.setdefault(game_id, {"attempts": 0})
        entry.update(
            {
                "status": STATUS_FAILED,
                "last_action": ACTION_FAILED,
                "error_type": error_type,
                "error": error[:500],  # keep manifests readable
                "attempts": int(entry.get("attempts", 0)) + 1,
                "updated_at": _now(),
            }
        )
        self._append_failure_log(game_id, error_type, error, entry["attempts"])

    def _append_failure_log(self, game_id: str, error_type: str, error: str, attempts: int) -> None:
        ensure_dir(MANIFEST_DIR)
        record = {
            "timestamp": _now(),
            "season": self.season,
            "season_type": self.season_type,
            "game_id": game_id,
            "error_type": error_type,
            "error": error[:500],
            "attempts": attempts,
        }
        with failure_log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    # --- Querying ----------------------------------------------------------

    def game_ids_with_status(self, status: str) -> list[str]:
        return sorted(gid for gid, entry in self.games.items() if entry.get("status") == status)

    @property
    def pending(self) -> list[str]:
        return self.game_ids_with_status(STATUS_PENDING)

    @property
    def downloaded(self) -> list[str]:
        return self.game_ids_with_status(STATUS_DOWNLOADED)

    @property
    def failed(self) -> list[str]:
        return self.game_ids_with_status(STATUS_FAILED)

    def games_needing_download(self, include_failed: bool = True) -> list[str]:
        """Games still to attempt: pending, plus failed ones when retrying."""
        wanted = {STATUS_PENDING} | ({STATUS_FAILED} if include_failed else set())
        return sorted(gid for gid, entry in self.games.items() if entry.get("status") in wanted)

    def total_events(self) -> int:
        return sum(int(entry.get("event_count", 0)) for entry in self.games.values())

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.games),
            STATUS_PENDING: len(self.pending),
            STATUS_DOWNLOADED: len(self.downloaded),
            STATUS_FAILED: len(self.failed),
            "events": self.total_events(),
        }
