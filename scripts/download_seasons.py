"""Bulk-download historical play-by-play for one or more NBA seasons.

Examples
--------
Download three seasons (each finishes before the next starts)::

    python scripts/download_seasons.py --seasons 2021-22 2022-23 2023-24

Try a handful of games first, which is the sane way to start::

    python scripts/download_seasons.py --seasons 2023-24 --limit 3

Re-run the same command after an interruption and it resumes: games already on
disk and valid are skipped.

Retry only the games that previously failed::

    python scripts/download_seasons.py --seasons 2023-24 --only-failed

Show what the manifest currently knows without downloading anything::

    python scripts/download_seasons.py --seasons 2023-24 --status
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.downloader import (  # noqa: E402
    DEFAULT_BULK_SPACING,
    DEFAULT_PROGRESS_EVERY,
    DownloadStats,
    download_season,
    get_season_game_ids,
)
from src.data.manifest import DownloadManifest, manifest_path  # noqa: E402
from src.paths import PLAY_BY_PLAY_DIR  # noqa: E402

logger = logging.getLogger("download")


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # The retry wrapper logs one line per request; at bulk scale that is noise
    # unless something is going wrong, so only surface warnings and above.
    logging.getLogger("src.data.nba_client").setLevel(
        logging.DEBUG if verbose else logging.WARNING
    )
    logging.getLogger("src.data.play_by_play").setLevel(
        logging.DEBUG if verbose else logging.WARNING
    )


def show_status(season: str, season_type: str) -> None:
    """Print what the manifest knows about a season without downloading."""
    manifest = DownloadManifest.load(season, season_type)
    summary = manifest.summary()

    print(f"\n=== {season} {season_type} ===")
    print(f"manifest : {manifest_path(season, season_type)}")
    if not manifest.games:
        print("No manifest yet - nothing downloaded for this season.")
        return

    print(f"total      : {summary['total']}")
    print(f"downloaded : {summary['downloaded']}")
    print(f"pending    : {summary['pending']}")
    print(f"failed     : {summary['failed']}")
    print(f"events     : {summary['events']:,}")

    if manifest.failed:
        print(f"\nFailed GAME_IDs ({len(manifest.failed)}):")
        for game_id in manifest.failed[:20]:
            entry = manifest.games[game_id]
            print(f"  {game_id}  {entry.get('error_type', '?')}: {entry.get('error', '')[:90]}")
        if len(manifest.failed) > 20:
            print(f"  ... and {len(manifest.failed) - 20} more")


def disk_usage_mb() -> tuple[float, int]:
    """Total size and file count of raw play-by-play on disk (both formats)."""
    if not PLAY_BY_PLAY_DIR.exists():
        return 0.0, 0
    files = list(PLAY_BY_PLAY_DIR.glob("*.json.gz")) + list(PLAY_BY_PLAY_DIR.glob("*.json"))
    total = sum(path.stat().st_size for path in files)
    return total / (1024 * 1024), len(files)


def print_report(results: list[DownloadStats]) -> None:
    print(f"\n{'=' * 78}\nDOWNLOAD REPORT\n{'=' * 78}")
    header = f"{'season':<10}{'total':>8}{'run':>8}{'dl':>8}{'skip':>8}{'fail':>8}{'events':>10}{'time':>10}"
    print(header)
    print("-" * len(header))

    for stats in results:
        print(
            f"{stats.season:<10}{stats.total:>8}{stats.considered:>8}{stats.downloaded:>8}"
            f"{stats.skipped:>8}{stats.failed:>8}{stats.events:>10,}{stats.elapsed_seconds:>9.1f}s"
        )

    total_failed = sum(stats.failed for stats in results)
    used_mb, file_count = disk_usage_mb()
    print(f"\nRaw play-by-play on disk: {used_mb:.1f} MB across {file_count:,} files")

    if total_failed:
        print(f"\n{total_failed} game(s) failed. Retry them with --only-failed")
        for stats in results:
            for game_id in stats.failed_game_ids[:10]:
                print(f"  {stats.season}  {game_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk-download NBA historical play-by-play")
    parser.add_argument("--seasons", nargs="+", required=True, help="e.g. 2021-22 2022-23")
    parser.add_argument("--season-type", default="Regular Season")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N games (for testing)")
    parser.add_argument("--only-failed", action="store_true", help="Retry previously failed games only")
    parser.add_argument("--status", action="store_true", help="Report manifest state and exit")
    parser.add_argument("--save-csv", action="store_true", help="Also write a flat CSV per game")
    parser.add_argument("--spacing", type=float, default=DEFAULT_BULK_SPACING, help="Seconds between requests")
    parser.add_argument("--progress-every", type=int, default=DEFAULT_PROGRESS_EVERY)
    parser.add_argument("--refresh-game-log", action="store_true", help="Re-fetch season game logs")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.status:
        for season in args.seasons:
            show_status(season, args.season_type)
        return 0

    results: list[DownloadStats] = []
    for season in args.seasons:
        only_game_ids = None
        if args.only_failed:
            manifest = DownloadManifest.load(season, args.season_type)
            only_game_ids = manifest.failed
            if not only_game_ids:
                logger.info("[%s] no failed games to retry", season)
                continue
            logger.info("[%s] retrying %d failed games", season, len(only_game_ids))

        results.append(
            download_season(
                season=season,
                season_type=args.season_type,
                limit=args.limit,
                retry_failed=True,
                save_csv=args.save_csv,
                spacing=args.spacing,
                progress_every=args.progress_every,
                refresh_game_log=args.refresh_game_log,
                only_game_ids=only_game_ids,
            )
        )

    print_report(results)

    # Non-zero exit when anything failed, so this is usable in a shell pipeline.
    return 1 if any(stats.failed for stats in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
