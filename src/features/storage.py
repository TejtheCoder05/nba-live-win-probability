"""Partitioned, atomic Parquet storage for independently rebuildable games."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.paths import PROCESSED_DIR, ensure_dir

GAME_STATES_DIR = PROCESSED_DIR / "game_states"


def season_states_dir(season: str) -> Path:
    return GAME_STATES_DIR / f"season={season}"


def game_states_path(season: str, game_id: str) -> Path:
    return season_states_dir(season) / f"{game_id}.parquet"


def write_game_states(frame: pd.DataFrame, season: str, game_id: str) -> Path:
    """Atomically replace one game's Parquet partition."""
    directory = ensure_dir(season_states_dir(season))
    path = directory / f"{game_id}.parquet"
    temporary = directory / f"{game_id}.parquet.tmp"
    frame.to_parquet(temporary, index=False, engine="pyarrow", compression="zstd")
    temporary.replace(path)
    return path


def load_game_states(season: str, game_id: str) -> pd.DataFrame:
    return pd.read_parquet(game_states_path(season, game_id), engine="pyarrow")
