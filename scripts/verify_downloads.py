"""Independently verify everything downloaded for a season.

The downloader validates each response as it arrives. This script re-validates
the whole season from scratch, afterwards, from the files on disk — deliberately
*not* trusting the manifest. It is the answer to "is this dataset actually
complete and usable?" before we start building features on it.

Run:
    python scripts/verify_downloads.py --seasons 2023-24
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.downloader import get_season_game_ids  # noqa: E402
from src.data.manifest import DownloadManifest  # noqa: E402
from src.data.play_by_play import existing_raw_path  # noqa: E402
from src.data.validation import validate_cached_file  # noqa: E402


def verify_season(season: str, season_type: str) -> bool:
    print(f"\n{'=' * 78}\nVERIFYING {season} {season_type}\n{'=' * 78}")

    game_ids, index = get_season_game_ids(season, season_type)
    manifest = DownloadManifest.load(season, season_type)

    print(f"Games in season game log : {len(game_ids)}")
    print(f"Games in manifest        : {len(manifest.games)}")

    missing: list[str] = []
    invalid: list[tuple[str, str]] = []
    total_events = 0
    total_bytes = 0
    periods_seen: Counter[int] = Counter()

    for position, game_id in enumerate(game_ids, start=1):
        path = existing_raw_path(game_id)
        if path is None:
            missing.append(game_id)
            continue

        result = validate_cached_file(path, game_id)
        if not result.is_valid:
            invalid.append((game_id, result.reason))
            continue

        total_events += result.event_count
        total_bytes += path.stat().st_size

        if position % 250 == 0:
            print(f"  ...verified {position}/{len(game_ids)}")

    verified = len(game_ids) - len(missing) - len(invalid)

    print(f"\nVerified OK      : {verified}/{len(game_ids)}")
    print(f"Missing          : {len(missing)}")
    print(f"Invalid          : {len(invalid)}")
    print(f"Total events     : {total_events:,}")
    print(f"Disk usage       : {total_bytes / (1024 * 1024):.1f} MB")
    if verified:
        print(f"Mean events/game : {total_events / verified:.1f}")

    # The label side of the eventual dataset, sanity-checked here too.
    print(f"\nLabel check (from the game log, not play-by-play):")
    print(f"  home wins      : {int(index['home_win'].sum())}")
    print(f"  home win rate  : {index['home_win'].mean():.3f}")

    if missing:
        print(f"\nMissing GAME_IDs (first 10): {missing[:10]}")
    if invalid:
        print("\nInvalid files (first 10):")
        for game_id, reason in invalid[:10]:
            print(f"  {game_id}: {reason}")

    ok = not missing and not invalid
    print(f"\nRESULT: {'PASS - season is complete and valid' if ok else 'INCOMPLETE'}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify downloaded seasons")
    parser.add_argument("--seasons", nargs="+", required=True)
    parser.add_argument("--season-type", default="Regular Season")
    args = parser.parse_args()

    results = [verify_season(season, args.season_type) for season in args.seasons]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
