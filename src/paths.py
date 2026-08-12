"""Canonical filesystem locations for the project.

Every module resolves paths from here instead of using relative paths, so that
scripts behave identically no matter which directory they are run from.
"""

from __future__ import annotations

from pathlib import Path

# src/paths.py -> src/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
LIVE_DATA_DIR = DATA_DIR / "live"

GAMELOG_DIR = RAW_DIR / "gamelogs"
PLAY_BY_PLAY_DIR = RAW_DIR / "playbyplay"
MANIFEST_DIR = RAW_DIR / "manifest"

ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
MODEL_ARTIFACT_DIR = ARTIFACTS_DIR / "win_probability_v1"
TESTS_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures"
LIVE_FIXTURE_DIR = TESTS_FIXTURE_DIR / "live"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (and parents) if needed and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
