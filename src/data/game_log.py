"""Retrieve the list of NBA games for a season via ``LeagueGameLog``.

This module answers one question: *which games happened, and how did they end?*
It deliberately knows nothing about play-by-play, features, or models.

The key wrinkle it handles is that ``LeagueGameLog`` with ``PlayerOrTeam="T"``
returns **one row per team per game**, so a single game appears twice. Every
downstream consumer wants games, not team-games, so this module provides both a
deduplicated GAME_ID list and a collapsed one-row-per-game index.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import LeagueGameLog

from src.data.nba_client import call_endpoint
from src.paths import GAMELOG_DIR, ensure_dir

logger = logging.getLogger(__name__)

# In the MATCHUP column, the home team's row reads "IND vs. CLE" and the away
# team's row reads "CLE @ IND". The presence of "@" is what marks a road game.
AWAY_MATCHUP_TOKEN = "@"


def fetch_season_game_log(
    season: str = "2023-24",
    season_type: str = "Regular Season",
) -> pd.DataFrame:
    """Fetch the team-level game log for one season as a DataFrame.

    ``season`` uses the NBA's hyphenated format, e.g. ``"2023-24"`` means the
    season that began in autumn 2023.
    """
    logger.info("Fetching %s %s game log", season, season_type)
    endpoint = call_endpoint(
        LeagueGameLog,
        season=season,
        season_type_all_star=season_type,
        player_or_team_abbreviation="T",  # team rows, not player rows
    )
    # Use the *named* data set rather than get_data_frames()[0]; the named
    # accessor keeps working if the endpoint ever returns extra data sets.
    return endpoint.league_game_log.get_data_frame()


def unique_game_ids(game_log: pd.DataFrame) -> list[str]:
    """Return the sorted, deduplicated GAME_ID values from a game log.

    Deduplication is essential and not merely defensive: the team-level game log
    contains two rows for every game (one per team), so the raw row count is
    exactly twice the number of games played.
    """
    return sorted(game_log["GAME_ID"].dropna().unique().tolist())


def build_game_index(game_log: pd.DataFrame) -> pd.DataFrame:
    """Collapse the two team rows per game into one row per game.

    Produces the columns later phases need in order to *label* training data:

    ``GAME_ID, GAME_DATE, home_team, away_team, home_pts, away_pts, home_win``

    ``home_win`` is 1 when the home team won and 0 when it lost. This is the
    target the model will eventually be trained against; it comes straight from
    the final score, not from any NBA-provided win-probability figure.
    """
    is_away = game_log["MATCHUP"].str.contains(AWAY_MATCHUP_TOKEN, regex=False)

    home_rows = game_log.loc[~is_away, ["GAME_ID", "GAME_DATE", "TEAM_ID", "TEAM_ABBREVIATION", "PTS"]]
    away_rows = game_log.loc[is_away, ["GAME_ID", "TEAM_ID", "TEAM_ABBREVIATION", "PTS"]]

    home_rows = home_rows.rename(
        columns={"TEAM_ID": "home_team_id", "TEAM_ABBREVIATION": "home_team", "PTS": "home_pts"}
    )
    away_rows = away_rows.rename(
        columns={"TEAM_ID": "away_team_id", "TEAM_ABBREVIATION": "away_team", "PTS": "away_pts"}
    )

    index = home_rows.merge(away_rows, on="GAME_ID", how="inner")
    index["home_win"] = (index["home_pts"] > index["away_pts"]).astype("int8")
    return index.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)


def gamelog_cache_path(season: str, season_type: str) -> Path:
    """Local cache location for one season's game log."""
    slug = season_type.lower().replace(" ", "_")
    return GAMELOG_DIR / f"{season}_{slug}.csv"


def save_game_log(game_log: pd.DataFrame, season: str, season_type: str) -> Path:
    """Cache a season game log to CSV so re-runs need not re-hit the API."""
    ensure_dir(GAMELOG_DIR)
    path = gamelog_cache_path(season, season_type)
    game_log.to_csv(path, index=False)
    logger.info("Saved game log -> %s", path)
    return path


def load_cached_game_log(season: str, season_type: str) -> pd.DataFrame | None:
    """Return a cached game log if one exists, else ``None``.

    GAME_ID is read as a string on purpose: NBA game IDs are zero-padded
    (``"0022300001"``), and letting pandas infer an integer would silently
    destroy the leading zeros.
    """
    path = gamelog_cache_path(season, season_type)
    if not path.exists():
        return None
    logger.info("Loading cached game log <- %s", path)
    return pd.read_csv(path, dtype={"GAME_ID": str})
