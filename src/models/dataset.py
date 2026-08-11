"""Chronological, game-isolated model datasets built from Phase 3 states."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from src.features.storage import season_states_dir

TRAIN_SEASON = "2021-22"
VALIDATION_SEASON = "2022-23"
TEST_SEASON = "2023-24"
SPLIT_SEASONS = {
    "train": TRAIN_SEASON,
    "validation": VALIDATION_SEASON,
    "test": TEST_SEASON,
}

# Deliberately excludes IDs, text/event semantics, team identity, and target.
V1_FEATURES = (
    "score_differential",
    "period",
    "seconds_remaining_period",
    "seconds_remaining_regulation",
    "is_overtime",
    "overtime_number",
    "home_possession",
    "possession_known",
    "home_team_fouls_period",
    "away_team_fouls_period",
)
TARGET_COLUMN = "home_win"
AUDIT_ONLY_CANDIDATES = ("clock", "home_score", "away_score")
MODEL_COLUMNS = ("season", "game_id", *V1_FEATURES, *AUDIT_ONLY_CANDIDATES, TARGET_COLUMN)


@dataclass(frozen=True)
class SeasonSplit:
    name: str
    season: str
    frame: pd.DataFrame

    @property
    def game_ids(self) -> frozenset[str]:
        return frozenset(self.frame["game_id"].astype(str).unique())


def parquet_files(season: str, root: Path | None = None) -> list[Path]:
    """Return deterministic per-game files without Hive partition inference.

    The Parquet files already contain a ``season`` column. Reading the parent
    ``season=...`` directory as a dataset would make PyArrow also infer a Hive
    partition column with a conflicting type, so files are read explicitly.
    """
    directory = (root / f"season={season}") if root else season_states_dir(season)
    files = sorted(directory.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No processed game states found in {directory}")
    return files


def load_season_states(
    season: str,
    columns: Iterable[str] = MODEL_COLUMNS,
    root: Path | None = None,
) -> pd.DataFrame:
    """Load one chronological split from its independently stored games."""
    selected = list(columns)
    frames = [pd.read_parquet(path, columns=selected, engine="pyarrow") for path in parquet_files(season, root)]
    result = pd.concat(frames, ignore_index=True, copy=False)
    if "season" in result and set(result["season"].astype(str).unique()) != {season}:
        raise ValueError(f"Season partition {season} contains a different season")
    return result


def make_split(name: str, root: Path | None = None) -> SeasonSplit:
    if name not in SPLIT_SEASONS:
        raise ValueError(f"Unknown split {name!r}; expected one of {tuple(SPLIT_SEASONS)}")
    season = SPLIT_SEASONS[name]
    return SeasonSplit(name=name, season=season, frame=load_season_states(season, root=root))


def assert_isolated_splits(splits: Iterable[SeasonSplit]) -> None:
    """Prove each game belongs to exactly one chronological season split."""
    items = list(splits)
    for index, left in enumerate(items):
        expected = SPLIT_SEASONS.get(left.name)
        if expected != left.season:
            raise ValueError(f"{left.name} must use {expected}, received {left.season}")
        materialized_seasons = set(left.frame["season"].astype(str).unique())
        if materialized_seasons != {left.season}:
            raise ValueError(
                f"{left.name} frame must contain only {left.season}; received {sorted(materialized_seasons)}"
            )
        for right in items[index + 1 :]:
            overlap = left.game_ids & right.game_ids
            if overlap:
                raise ValueError(f"Game overlap between {left.name} and {right.name}: {sorted(overlap)[:3]}")


def features_and_target(
    frame: pd.DataFrame,
    *,
    features: Iterable[str] = V1_FEATURES,
    drop_unknown_possession: bool = False,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Extract model inputs/target without mutating the source frame."""
    ordered = tuple(features)
    if TARGET_COLUMN in ordered:
        raise ValueError("Target leakage: home_win cannot be a model feature")
    missing = [name for name in (*ordered, TARGET_COLUMN) if name not in frame]
    if missing:
        raise KeyError(f"Missing dataset columns: {missing}")
    selected = frame.loc[frame["possession_known"].astype(bool)] if drop_unknown_possession else frame
    return selected.loc[:, ordered].copy(), selected[TARGET_COLUMN].to_numpy(dtype=np.float32, copy=True)


def game_balanced_weights(frame: pd.DataFrame) -> np.ndarray:
    """Give every game equal total weight, normalized to mean row weight one."""
    counts = frame.groupby("game_id", observed=True)["game_id"].transform("size").to_numpy(dtype=np.float64)
    weights = 1.0 / counts
    weights /= weights.mean()
    return weights.astype(np.float32)


def time_bucket_sample(frame: pd.DataFrame, seconds: int = 30) -> pd.DataFrame:
    """Keep the last emitted state in each elapsed-game-time bucket."""
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    period = frame["period"].to_numpy(dtype=np.int16)
    remaining = frame["seconds_remaining_period"].to_numpy(dtype=np.float64)
    elapsed = np.where(
        period <= 4,
        (period - 1) * 720 + (720 - remaining),
        2880 + (period - 5) * 300 + (300 - remaining),
    )
    working = frame.assign(_time_bucket=np.floor(elapsed / seconds).astype(np.int32))
    sampled = working.groupby(["game_id", "_time_bucket"], sort=False, observed=True).tail(1)
    return sampled.drop(columns="_time_bucket").reset_index(drop=True)


def dataset_summary(frame: pd.DataFrame, features: Iterable[str] = V1_FEATURES) -> dict[str, object]:
    ordered = list(features)
    games = int(frame["game_id"].nunique())
    labels_by_game = frame.groupby("game_id", observed=True)[TARGET_COLUMN].first()
    ranges: dict[str, dict[str, float | None]] = {}
    for name in ordered:
        values = pd.to_numeric(frame[name], errors="coerce")
        ranges[name] = {
            "min": None if values.notna().sum() == 0 else float(values.min()),
            "max": None if values.notna().sum() == 0 else float(values.max()),
        }
    return {
        "rows": int(len(frame)),
        "games": games,
        "row_home_win_rate": float(frame[TARGET_COLUMN].mean()),
        "game_home_win_rate": float(labels_by_game.mean()),
        "possession_known_rate": float(frame["possession_known"].mean()),
        "mean_states_per_game": float(len(frame) / games),
        "missing_values": {name: int(frame[name].isna().sum()) for name in ordered},
        "feature_ranges": ranges,
    }


def state_distribution(frame: pd.DataFrame) -> Mapping[str, object]:
    counts = frame.groupby("game_id", observed=True).size()
    return {
        "states_per_game": {
            "min": int(counts.min()),
            "mean": float(counts.mean()),
            "median": float(counts.median()),
            "p95": float(counts.quantile(0.95)),
            "max": int(counts.max()),
        },
        "states_by_period": {str(key): int(value) for key, value in frame.groupby("period").size().items()},
        "states_by_time_remaining": {
            key: int(value)
            for key, value in pd.cut(
                frame["seconds_remaining_regulation"],
                bins=[-1, 120, 360, 720, 1440, 2160, np.inf],
                labels=["0-2m", "2-6m", "6-12m", "12-24m", "24-36m", ">36m"],
            ).value_counts(sort=False).items()
        },
        "states_by_abs_score_differential": {
            key: int(value)
            for key, value in pd.cut(
                frame["score_differential"].abs(),
                bins=[-1, 2, 5, 10, 20, np.inf],
                labels=["0-2", "3-5", "6-10", "11-20", "20+"],
            ).value_counts(sort=False).items()
        },
    }
