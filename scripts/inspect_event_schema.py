"""Inventory historical PlayByPlayV3 fields and event values by season.

This is deliberately a streaming inspection: it reads one gzipped game at a
time and keeps only counters plus a few examples.  Phase 3 event semantics must
be based on the downloaded data, not on stale ``nba_api`` metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.downloader import get_season_game_ids  # noqa: E402
from src.data.play_by_play import existing_raw_path, read_raw_json  # noqa: E402
from src.paths import PROJECT_ROOT  # noqa: E402

TEAM_FOUL_MARKER = re.compile(r"\.T(\d+)\)", re.IGNORECASE)
MAX_EXAMPLES = 8

TECHNICAL_FOUL_SUBTYPES = {
    "technical",
    "double technical",
    "delay technical",
    "non-unsportsmanlike technical",
    "hanging technical",
    "too many players technical",
    "excess timeout technical",
    "defense 3 second",
    "flopping",
    "bench",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _remember(examples: dict[str, list[dict[str, Any]]], key: str, item: dict[str, Any]) -> None:
    if len(examples[key]) < MAX_EXAMPLES:
        examples[key].append(item)


def inspect_season(season: str, season_type: str) -> dict[str, Any]:
    game_ids, _ = get_season_game_ids(season, season_type)
    fields: Counter[str] = Counter()
    action_types: Counter[str] = Counter()
    subtypes: dict[str, Counter[str]] = defaultdict(Counter)
    blank_action_descriptions: Counter[str] = Counter()
    foul_markers: Counter[int] = Counter()
    foul_marker_coverage: dict[str, Counter[str]] = defaultdict(Counter)
    foul_candidate_counts: dict[str, dict[tuple[str, int, int], int]] = {
        "exclude_technical": defaultdict(int),
        "exclude_technical_and_offensive": defaultdict(int),
    }
    foul_candidate_checks: dict[str, Counter[str]] = defaultdict(Counter)
    timeout_team_ids: Counter[str] = Counter()
    timeout_tricodes: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    period_counts: Counter[int] = Counter()
    event_count = 0
    missing_files: list[str] = []

    for position, game_id in enumerate(game_ids, start=1):
        path = existing_raw_path(game_id)
        if path is None:
            missing_files.append(game_id)
            continue
        actions = read_raw_json(path)["game"]["actions"]
        event_count += len(actions)

        for action in actions:
            fields.update(action.keys())
            action_type = _text(action.get("actionType"))
            subtype = _text(action.get("subType"))
            description = _text(action.get("description"))
            normalized_type = action_type.lower() or "<blank>"
            normalized_subtype = subtype.lower() or "<blank>"
            action_types[normalized_type] += 1
            subtypes[normalized_type][normalized_subtype] += 1

            period = action.get("period")
            if isinstance(period, int):
                period_counts[period] += 1

            sample = {
                "game_id": game_id,
                "action_number": action.get("actionNumber"),
                "period": period,
                "clock": action.get("clock"),
                "team_id": action.get("teamId"),
                "team_tricode": action.get("teamTricode"),
                "action_type": action_type,
                "subtype": subtype,
                "description": description,
            }

            if not action_type:
                blank_action_descriptions[description] += 1
                _remember(examples, "blank_action_type", sample)

            if "foul" in normalized_type:
                _remember(examples, f"foul:{normalized_subtype}", sample)
                marker = TEAM_FOUL_MARKER.search(description)
                if marker:
                    foul_markers[int(marker.group(1))] += 1
                    foul_marker_coverage[normalized_subtype]["with_marker"] += 1
                else:
                    foul_marker_coverage[normalized_subtype]["without_marker"] += 1

                team_id = action.get("teamId")
                if isinstance(team_id, int) and team_id > 0 and isinstance(period, int):
                    key = (game_id, period, team_id)
                    candidates = {
                        "exclude_technical": normalized_subtype not in TECHNICAL_FOUL_SUBTYPES,
                        "exclude_technical_and_offensive": (
                            normalized_subtype not in TECHNICAL_FOUL_SUBTYPES
                            and normalized_subtype not in {"offensive", "offensive charge"}
                        ),
                    }
                    for candidate, should_count in candidates.items():
                        if should_count:
                            foul_candidate_counts[candidate][key] += 1
                        if marker:
                            expected = foul_candidate_counts[candidate][key]
                            observed = int(marker.group(1))
                            label = "match" if expected == observed else "mismatch"
                            foul_candidate_checks[candidate][label] += 1
                            if label == "mismatch":
                                mismatch = dict(sample)
                                mismatch.update(expected=expected, observed=observed)
                                _remember(examples, f"foul_mismatch:{candidate}", mismatch)

            if "free throw" in normalized_type:
                _remember(examples, f"free_throw:{normalized_subtype}", sample)

            if "rebound" in normalized_type:
                _remember(examples, f"rebound:{normalized_subtype}", sample)

            if "jump ball" in normalized_type:
                _remember(examples, "jump_ball", sample)

            if "timeout" in normalized_type:
                timeout_team_ids[_text(action.get("teamId")) or "<blank>"] += 1
                timeout_tricodes[_text(action.get("teamTricode")) or "<blank>"] += 1
                _remember(examples, f"timeout:{normalized_subtype}", sample)

        if position % 250 == 0:
            print(f"[{season}] inspected {position}/{len(game_ids)}", file=sys.stderr)

    return {
        "season": season,
        "season_type": season_type,
        "games": len(game_ids),
        "events": event_count,
        "missing_files": missing_files,
        "field_presence": dict(sorted(fields.items())),
        "action_types": dict(action_types.most_common()),
        "subtypes_by_action": {
            action_type: dict(counter.most_common())
            for action_type, counter in sorted(subtypes.items())
        },
        "blank_action_descriptions": dict(blank_action_descriptions.most_common(30)),
        "period_counts": {str(key): value for key, value in sorted(period_counts.items())},
        "foul_marker_counts": {str(key): value for key, value in sorted(foul_markers.items())},
        "foul_marker_coverage_by_subtype": {
            subtype: dict(counter) for subtype, counter in sorted(foul_marker_coverage.items())
        },
        "foul_count_candidate_checks": {
            candidate: dict(counter) for candidate, counter in sorted(foul_candidate_checks.items())
        },
        "timeout_team_ids": dict(timeout_team_ids.most_common()),
        "timeout_tricodes": dict(timeout_tricodes.most_common()),
        "examples": dict(sorted(examples.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect historical PlayByPlayV3 event schemas")
    parser.add_argument("--seasons", nargs="+", required=True)
    parser.add_argument("--season-type", default="Regular Season")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed" / "event_schema_inventory.json",
    )
    args = parser.parse_args()

    report = {
        "seasons": [inspect_season(season, args.season_type) for season in args.seasons]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
