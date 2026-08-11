"""Phase 1 proof of concept.

Proves end to end that we can:

1. pull one completed NBA regular season's game list from ``LeagueGameLog``
2. deduplicate it down to unique GAME_IDs
3. pull one game's play-by-play from ``PlayByPlayV3``
4. save that game's raw data locally
5. observe the *actual* fields the API returns, so later feature work is based
   on reality rather than on assumptions about column names

Run:
    python scripts/phase1_poc.py --season 2023-24
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

# Allow `python scripts/phase1_poc.py` to import `src` without installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.game_log import (  # noqa: E402
    build_game_index,
    fetch_season_game_log,
    load_cached_game_log,
    save_game_log,
    unique_game_ids,
)
from src.data.play_by_play import (  # noqa: E402
    fetch_play_by_play,
    save_raw_play_by_play,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase1")

# Columns we care most about when eyeballing the event stream.
PREVIEW_COLUMNS = [
    "actionNumber",
    "period",
    "clock",
    "teamTricode",
    "scoreHome",
    "scoreAway",
    "actionType",
    "subType",
    "description",
]


def banner(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def show_game_log(season: str, season_type: str, refresh: bool) -> pd.DataFrame:
    """Step 1-2: fetch (or reuse) the season game log and inspect its structure."""
    banner(f"STEP 1 - Season game log: {season} {season_type}")

    game_log = None if refresh else load_cached_game_log(season, season_type)
    if game_log is None:
        game_log = fetch_season_game_log(season=season, season_type=season_type)
        save_game_log(game_log, season, season_type)
    else:
        print("(using cached copy from data/raw/gamelogs/)")

    print(f"\nDataFrame shape : {game_log.shape}")
    print(f"Columns ({len(game_log.columns)}): {list(game_log.columns)}")
    print("\nFirst 3 rows:")
    print(game_log[["SEASON_ID", "GAME_ID", "GAME_DATE", "MATCHUP", "WL", "PTS"]].head(3).to_string(index=False))
    return game_log


def show_unique_game_ids(game_log: pd.DataFrame) -> list[str]:
    """Step 3: deduplicate GAME_IDs and demonstrate why that is necessary."""
    banner("STEP 2 - Unique GAME_IDs (why deduplication matters)")

    game_ids = unique_game_ids(game_log)
    rows = len(game_log)
    print(f"Rows returned by LeagueGameLog : {rows}")
    print(f"Unique GAME_ID values          : {len(game_ids)}")
    print(f"Rows per game                  : {rows / len(game_ids):.1f}")
    print(
        "\nThe game log is TEAM-level, so each game appears once per team.\n"
        "Deduplicating GAME_ID is what turns team-games into games."
    )

    sample_id = game_ids[0]
    print(f"\nBoth rows for GAME_ID {sample_id}:")
    print(
        game_log.loc[game_log["GAME_ID"] == sample_id, ["GAME_ID", "TEAM_ABBREVIATION", "MATCHUP", "WL", "PTS"]]
        .to_string(index=False)
    )
    return game_ids


def show_game_index(game_log: pd.DataFrame) -> pd.DataFrame:
    """Step 4: collapse to one row per game and derive the future label."""
    banner("STEP 3 - One row per game, with the eventual training label")

    index = build_game_index(game_log)
    print(f"Game index shape: {index.shape}")
    print("\nFirst 5 games:")
    print(
        index[["GAME_ID", "GAME_DATE", "away_team", "home_team", "away_pts", "home_pts", "home_win"]]
        .head(5)
        .to_string(index=False)
    )
    print(f"\nHome win rate this season: {index['home_win'].mean():.3f}")
    print("(A sanity check: NBA home teams historically win ~55-58% of games.)")
    return index


def show_play_by_play(game_id: str) -> pd.DataFrame:
    """Steps 5-7: fetch, save, and inspect one game's play-by-play."""
    banner(f"STEP 4 - Play-by-play for GAME_ID {game_id}")

    events, raw = fetch_play_by_play(game_id)
    json_path, csv_path = save_raw_play_by_play(game_id, events, raw)

    print(f"\nSelected GAME_ID        : {game_id}")
    print(f"Play-by-play events     : {len(events)}")
    print(f"Columns returned ({len(events.columns)}) :")
    for i, col in enumerate(events.columns, 1):
        print(f"  {i:2d}. {col}")

    print(f"\nSaved raw JSON -> {json_path}")
    print(f"Saved flat CSV -> {csv_path}")

    print("\nFirst 10 play-by-play events:")
    print(events[PREVIEW_COLUMNS].head(10).to_string(index=False))
    return events


def show_field_observations(events: pd.DataFrame) -> None:
    """Step 8: record the ACTUAL formats, so feature code is not guesswork."""
    banner("STEP 5 - Observed field formats (the point of the whole exercise)")

    print("clock -- sample values:")
    print(f"  {events['clock'].dropna().unique()[:5].tolist()}")
    print("  Format is an ISO-8601 duration: PT<minutes>M<seconds>S")
    print("  -> 'seconds remaining' must be PARSED from this, not read directly.\n")

    print("scoreHome / scoreAway -- dtype:", events["scoreHome"].dtype)
    blank_home = (events["scoreHome"].astype(str).str.strip() == "").sum()
    print(f"  Rows with an EMPTY scoreHome: {blank_home} of {len(events)}")
    print("  -> Scores are strings and are only populated on scoring plays.")
    print("  -> They must be forward-filled to get the score at every event.\n")

    print("period -- dtype:", events["period"].dtype, "| values:", sorted(events["period"].unique().tolist()))
    print()

    print("actionType -- distinct values and counts:")
    counts = events["actionType"].value_counts()
    for action, n in counts.items():
        print(f"  {str(action):<22} {n:>4}")
    print()

    print("Fouls are events, NOT a running count:")
    fouls = events[events["actionType"].astype(str).str.contains("Foul", case=False, na=False)]
    print(f"  Foul events in this game: {len(fouls)}")
    print(f"  Foul subTypes: {sorted(fouls['subType'].astype(str).unique().tolist())[:8]}")
    print("  -> 'team fouls this period' must be COUNTED per team per period.\n")

    print("Timeouts are events too:")
    timeouts = events[events["actionType"].astype(str).str.contains("Timeout", case=False, na=False)]
    print(f"  Timeout events in this game: {len(timeouts)}")
    print("  -> 'timeouts remaining' must be counted against NBA allowances.\n")

    print("There is NO possession column. Possession must be reconstructed from")
    print("the event stream (made shots, turnovers, rebounds, free throws, jump")
    print("balls, period boundaries). That is the Phase 3 possession engine.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 nba_api proof of concept")
    parser.add_argument("--season", default="2023-24", help="Season, e.g. 2023-24")
    parser.add_argument("--season-type", default="Regular Season")
    parser.add_argument("--game-id", default=None, help="Override which game to pull")
    parser.add_argument("--refresh", action="store_true", help="Ignore the cached game log")
    args = parser.parse_args()

    game_log = show_game_log(args.season, args.season_type, args.refresh)
    game_ids = show_unique_game_ids(game_log)
    index = show_game_index(game_log)

    # Pick the season's first completed game unless told otherwise. "Completed"
    # is guaranteed here because LeagueGameLog only returns games that finished.
    game_id = args.game_id or index.loc[0, "GAME_ID"]
    if game_id not in game_ids:
        logger.warning("GAME_ID %s is not in the %s game log", game_id, args.season)

    events = show_play_by_play(game_id)
    show_field_observations(events)

    banner("PHASE 1 COMPLETE")
    print(f"Season           : {args.season} {args.season_type}")
    print(f"Games in season  : {len(game_ids)}")
    print(f"Selected GAME_ID : {game_id}")
    print(f"Events retrieved : {len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
