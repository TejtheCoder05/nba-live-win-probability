"""Generate partitioned Phase 3 historical game-state Parquet files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.pipeline import process_season, write_processing_report  # noqa: E402
from src.paths import PROCESSED_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build historical game-state features")
    parser.add_argument("--seasons", nargs="+", required=True)
    parser.add_argument("--season-type", default="Regular Season")
    parser.add_argument("--game-ids", nargs="+")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument(
        "--report",
        type=Path,
        default=PROCESSED_DIR / "phase3_processing_report.json",
    )
    args = parser.parse_args()

    results = [
        process_season(
            season,
            season_type=args.season_type,
            game_ids=args.game_ids,
            limit=args.limit,
            progress_every=args.progress_every,
        )
        for season in args.seasons
    ]
    write_processing_report(results, args.report)

    failed_scores = 0
    for result in results:
        print(
            f"[{result.season}] games={result.games_processed} raw_events={result.raw_events:,} "
            f"states={result.emitted_states:,} known_possession={result.known_possession_percent:.2f}% "
            f"final_scores={result.final_scores_valid}/{result.games_processed} "
            f"foul_markers={result.foul_markers_matched}/{result.foul_markers_checked} "
            f"disk={result.processed_bytes / (1024 * 1024):.1f} MB "
            f"time={result.elapsed_seconds:.1f}s"
        )
        failed_scores += result.games_processed - result.final_scores_valid
    print(f"Wrote report: {args.report}")
    return 1 if failed_scores else 0


if __name__ == "__main__":
    raise SystemExit(main())
