# Phase 3 completion report

## Downloads and raw verification

| season | expected | downloaded | skipped | failed | events | mean/game | raw PBP disk | elapsed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021-22 | 1,230 | 1,230 | 0 | 0 | 600,718 | 488.4 | 16.9 MB | 1,271.1 s |
| 2022-23 | 1,230 | 1,230 | 0 | 0 | 603,785 | 490.9 | 17.1 MB | 1,243.1 s |
| 2023-24 | 1,230 | 0 | 1,230 cached | 0 | 598,705 | 486.8 | 20.4 MB | previously completed |

Independent raw verification passed 3,690/3,690 games with zero missing and
zero invalid files. Total raw play-by-play is 1,803,208 events and 54.5 MB of
compressed response files.

## Progressive validation

1. Known `0022300061`: 473 events, 366 states, final score valid, 23/23 foul
   markers, 94.0% known possession.
2. Ten edge-case games: double OT, close game, blowout, high fouls/free throws,
   technicals, flagrants, and team rebounds; 10/10 final scores and 304/304
   markers passed.
3. First 100 games: 49,915 events, 39,400 states, 100/100 final scores,
   2,787/2,787 markers passed.
4. Full 2023-24: 1,230/1,230 final scores passed after diagnosing one stale
   non-overturn replay snapshot.
5. All seasons: 3,690/3,690 final scores passed after diagnosing older-feed
   score resets and same-clock ordering anomalies.

## Final processed metrics

| season | games | raw events | emitted states | states/game | known possession | final scores | raw foul markers | Parquet | time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021-22 | 1,230 | 600,718 | 479,432 | 389.8 | 93.58% | 1,230/1,230 | 34,339/34,361 | 37.4 MB | 11.9 s |
| 2022-23 | 1,230 | 603,785 | 481,931 | 391.8 | 93.58% | 1,230/1,230 | 34,159/34,182 | 37.5 MB | 12.0 s |
| 2023-24 | 1,230 | 598,705 | 476,516 | 387.4 | 93.57% | 1,230/1,230 | 33,311/33,329 | 37.4 MB | 11.9 s |
| **total** | **3,690** | **1,803,208** | **1,437,879** | **389.7** | **93.57%** | **3,690/3,690** | **101,809/101,872** | **112.4 MB** | **35.8 s** |

Possession is known for 1,345,495 states and unknown for 92,384 (6.43%).
Unknown reasons are: 59,294 ambiguous/unattributed rebounds, 15,140 period
starts, 12,605 period ends, 3,505 unattributed team turnovers, and 1,840 jump
balls whose raw attribution does not reliably identify the winner.

Timeout features are excluded; see `EVENT_SCHEMA.md`. There are 3,690 Parquet
files, one per game.

## Exact columns

```text
season, game_id, source_event_index, action_number, action_id, clock,
action_type, sub_type, semantic_event, description, home_team_id,
away_team_id, home_score, away_score, score_differential, period,
seconds_remaining_period, seconds_remaining_regulation, is_overtime,
overtime_number, possession_team_id, home_possession, possession_known,
possession_reason, home_team_fouls_period, away_team_fouls_period,
foul_marker_team_count, foul_marker_matches, home_win
```

## Twenty consecutive states from `0022300061`

`home_possession=1` is Denver; `0` is the Lakers.

```csv
action_number,period,clock,event,home_score,away_score,diff,home_possession,home_fouls,away_fouls,reason
18,1,PT10M03.00S,missed shot,7,8,-1,0,0,0,missed field goal awaiting rebound
19,1,PT10M01.00S,rebound,7,8,-1,0,0,0,offensive rebound
20,1,PT09M49.00S,missed shot,7,8,-1,0,0,0,missed field goal awaiting rebound
21,1,PT09M46.00S,rebound,7,8,-1,1,0,0,defensive rebound
22,1,PT09M36.00S,missed shot,7,8,-1,1,0,0,missed field goal awaiting rebound
23,1,PT09M34.00S,rebound,7,8,-1,0,0,0,defensive rebound
24,1,PT09M30.00S,made shot,7,10,-3,1,0,0,made field goal
25,1,PT09M16.00S,missed shot,7,10,-3,1,0,0,missed field goal awaiting rebound
27,1,PT09M16.00S,made shot,9,10,-1,0,0,0,made field goal
28,1,PT09M16.00S,foul,9,10,-1,1,0,1,shooting foul
31,1,PT09M14.00S,rebound,9,10,-1,0,0,1,defensive rebound
32,1,PT09M02.00S,missed shot,9,10,-1,0,0,1,missed field goal awaiting rebound
33,1,PT08M58.00S,rebound,9,10,-1,1,0,1,defensive rebound
34,1,PT08M54.00S,made shot,11,10,1,0,0,1,made field goal
35,1,PT08M39.00S,missed shot,11,10,1,0,0,1,missed field goal awaiting rebound
36,1,PT08M36.00S,rebound,11,10,1,1,0,1,defensive rebound
37,1,PT08M30.00S,made shot,14,10,4,0,0,1,made field goal
39,1,PT08M14.00S,missed shot,14,10,4,0,0,1,missed field goal awaiting rebound
40,1,PT08M13.00S,rebound,14,10,4,1,0,1,defensive rebound
41,1,PT08M12.00S,turnover,14,10,4,0,0,1,turnover
```

## Interview explanation

Raw events cannot go directly into a model because scores are sparse strings,
clocks are duration text, many rows are metadata, and possession/foul state is
not supplied. Score is reconstructed by carrying the last trustworthy snapshot
forward from 0-0. The clock becomes numeric current-period and scheduled
regulation time without using knowledge of future overtime.

Possession needs memory: a miss does not end a possession, a rebound decides
what happens next, and a whole free-throw sequence is one dead-ball episode.
Looking only at the row before a rebound fails because BLOCK/STEAL credits and
substitutions can intervene. The engine instead retains the unresolved shooter.

Team fouls are counted from qualifying foul semantics and reset every period;
the description's `T#` is an independent audit signal rather than an input that
forces the result. Leakage is prevented because final scores and `home_win` do
not participate in state construction, and future overtime length is never
used.

Raw gzip JSON remains immutable evidence; typed Parquet is a regenerable ML
view. Keeping the state engine separate from historical storage is what will
allow later live inference to reuse the identical feature definitions and avoid
training/serving skew.
