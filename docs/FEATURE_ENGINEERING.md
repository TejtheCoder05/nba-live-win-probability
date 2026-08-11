# Phase 3 game-state reconstruction

## Reproduce

```bash
# Inspect real event values across all downloaded seasons
python scripts/inspect_event_schema.py --seasons 2021-22 2022-23 2023-24

# Generate one independently rebuildable Parquet file per game
python scripts/process_game_states.py --seasons 2021-22 2022-23 2023-24

# Verify every saved partition without trusting the processing report
python scripts/verify_game_states.py --seasons 2021-22 2022-23 2023-24
```

Output is `data/processed/game_states/season={season}/{GAME_ID}.parquet` with
Zstandard compression. Raw `.json.gz` files are never modified.

## State meaning

Each row is the state **after** its linked raw event. It includes the raw list
index, action number/ID, original clock, action/subtype, semantic category, and
description. This makes every derived value traceable.

The model-visible state is:

- home and away score plus home-minus-away differential;
- period and current-period seconds remaining;
- scheduled regulation seconds remaining;
- overtime flag and current overtime number;
- nullable possession team and home-possession indicator;
- home and away period team-foul counts.

`home_win` is a label, not a reconstruction input. Foul-marker fields and the
possession reason are diagnostics, not intended model features.

## Clock and leakage

Historical `PT06M07.00S` becomes 367 seconds. During regulation:

```text
(4 - current_period) * 720 + current_period_clock
```

Thus Q4 at 6:00 is 360 regulation seconds remaining. In overtime,
`seconds_remaining_regulation` is zero and only the current five-minute period
clock is retained. The processor never asks how many overtime periods the game
eventually contains, which would reveal future information.

## Score state

Blank score strings carry the previous score; leading blanks are 0-0. Explicit
scoring snapshots can also revise earlier entries. The anomaly policy in
`EVENT_SCHEMA.md` protects against stale/out-of-order snapshots using only data
available at the current event. Only after processing ends is the reconstructed
score compared with the game index.

## Possession state machine

| event | post-event behavior |
|---|---|
| Made field goal | opponent receives possession |
| Missed field goal | shooter remains pending until a rebound |
| Offensive rebound | rebound team keeps possession |
| Defensive rebound | rebound team gains possession |
| Turnover | opponent gains possession |
| Non-final normal free throw | shooting team remains in sequence |
| Made final normal free throw | opponent receives possession |
| Missed final normal free throw | remains pending until rebound |
| Technical free throw | preserves prior possession |
| Flagrant/clear-path final free throw | shooting team retains possession |
| Common/shooting foul | opponent of fouling team has possession |
| Offensive/charge foul | opponent of fouling team has possession |
| Period boundary | possession becomes unknown |
| Jump ball | unknown until a later establishing event |

The engine stores the unresolved missed shooter. Credit, substitution, timeout,
replay, and other metadata rows do not clear it, so a BLOCK/STEAL row between a
miss and rebound cannot break rebound classification.

When team ownership is absent (`teamId=0`) or no associated live miss is known,
possession becomes null. V1 does not guess from display text.

## Team fouls

Counts reset on every period, including overtime. Common personal, shooting,
loose-ball, take, transition-take, away-from-play, clear-path, flagrant, and
double-personal fouls count. Offensive/charge and technical-like subtypes do
not. The raw `(P#.T#)` marker is parsed after each foul and compared with the
independent count; discrepancies are reported but never forced to agree.

## Emission policy

Every raw event updates the engines, but a row is saved only when at least one
model-visible value differs from the preceding emitted state: clock, score,
period, possession, or team fouls. This removes duplicate BLOCK/STEAL credit,
substitution, replay, and display rows at the same game state while retaining
meaningful clock advancement and all state transitions.

## Why Parquet is separate from raw JSON

Raw compressed JSON is immutable evidence and lets new features be regenerated
without network calls. Parquet is typed, columnar, compressed, and much faster
for later ML column selection. Per-game partitions make a single game or season
replaceable without rebuilding the full corpus.

The same `src/features` state engine is intentionally independent of storage.
Later, a live-event adapter can feed normalized events through this engine so
training and inference cannot silently calculate features differently.
