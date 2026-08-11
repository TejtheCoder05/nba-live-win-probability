# Three-season PlayByPlayV3 inventory

This document is evidence from the locally downloaded 2021-22, 2022-23, and
2023-24 regular seasons. Regenerate the machine-readable inventory with:

```bash
python scripts/inspect_event_schema.py --seasons 2021-22 2022-23 2023-24
```

The scan covers 3,690 games and 1,803,208 events. Every event in every season
has the same 23 raw action fields:

`actionId`, `actionNumber`, `actionType`, `clock`, `description`,
`isFieldGoal`, `location`, `period`, `personId`, `playerName`, `playerNameI`,
`pointsTotal`, `scoreAway`, `scoreHome`, `shotDistance`, `shotResult`,
`shotValue`, `subType`, `teamId`, `teamTricode`, `videoAvailable`, `xLegacy`,
and `yLegacy`.

The response-level `gameId` is reattached by `events_from_raw()`.

## Action frequencies

| normalized actionType | 2021-22 | 2022-23 | 2023-24 |
|---|---:|---:|---:|
| Rebound | 128,916 | 126,592 | 126,490 |
| Missed Shot | 116,792 | 113,959 | 114,963 |
| Made Shot | 99,930 | 103,260 | 103,739 |
| Substitution | 57,427 | 57,341 | 57,675 |
| Free Throw | 53,781 | 57,881 | 53,430 |
| Foul | 49,837 | 50,635 | 47,646 |
| Turnover | 33,857 | 34,675 | 33,468 |
| blank | 30,366 | 29,381 | 31,036 |
| Timeout | 13,386 | 13,479 | 13,526 |
| Period | 9,974 | 10,018 | 9,974 |
| Jump Ball | 2,183 | 2,076 | 2,070 |
| Violation | 2,110 | 2,337 | 2,149 |
| Instant Replay | 2,064 | 2,067 | 2,457 |
| Ejection | 95 | 84 | 82 |

Blank `actionType` rows are overwhelmingly secondary BLOCK and STEAL credit
rows. They are not possessions of their own and can appear between a missed
shot and its rebound.

## Semantically important subtypes

- Free throws use `1 of 1`, `1/2 of 2`, and `1/2/3 of 3` forms. All seasons
  also contain unsuffixed technical free throws plus rare technical, flagrant,
  and clear-path multi-shot sequences.
- Rebound subtype is usually `Unknown`; it does **not** mean possession is
  unknowable. Player rebound descriptions include cumulative `(Off:# Def:#)`
  statistics, and `teamId` identifies most rebound teams. Team rebounds often
  have `teamId=0`, so they remain conservative unknowns in V1.
- Turnovers include bad pass, lost ball, offensive-foul turnover, out of bounds,
  shot clock, traveling, and rarer violation forms. Some team turnovers have
  `teamId=0`.
- Period rows are consistently `Start` or `End`.
- Timeout rows are `Regular` or `Coach Challenge`.
- Instant replay subtype vocabulary changes across seasons. `Replay Center`
  appears from 2022-23, and coach-challenge support/stands/overturn wording is
  not uniform.

## Season differences

- `Transition Take` foul appears in 2022-23 (212 events) and 2023-24 (150).
- 2021-22 instead contains far more `Personal Take` events (2,867 versus 862
  and 917), reflecting the rule/vocabulary transition.
- `Flopping` (79), `Bench` (1), and substantially more `Hanging Technical`
  fouls appear in 2023-24.
- Rare shot subtype labels change slightly, including bank-shot naming.
- Maximum observed period is 7 in 2021-22 and 6 in each later season.
- Clock representation is stable: all structural validation uses `PT#M#S`,
  including fractional seconds.

## Foul evidence

The independent description marker, for example `(P1.T3)`, is present on a
large subset of common fouls and only reports team counts 0 through 4. It is
usually absent after the team reaches the penalty.

Two candidate rules were tested against every available marker:

| rule | 2021-22 | 2022-23 | 2023-24 |
|---|---:|---:|---:|
| Exclude technical-like fouls only | 28,854/34,361 | 27,991/34,182 | 28,140/33,329 |
| Also exclude offensive/charge fouls | 34,339/34,361 | 34,159/34,182 | 33,311/33,329 |

The implemented second rule agrees with 101,809 of 101,872 markers (99.938%).
The 63 disagreements are retained in the processing report: 33 personal, 19
shooting, 4 loose-ball, 4 personal-take, 2 technical, and 1 flagrant type 1.
They are treated as source/revision discrepancies; the marker never overwrites
the independently reconstructed counter.

## Timeout decision

All 40,391 timeout events have `teamId=0` and blank `teamTricode`. Descriptions
contain display names in inconsistent case and mix `Full` and `Reg` counters.
That is insufficient evidence for a robust timeouts-remaining feature shared
with future live data. Timeout state is therefore intentionally excluded from
V1.

## Score and ordering anomalies

Scores are strings and usually blank, so they are forward-filled from 0-0.
The audit also found rare source anomalies:

- impossible resets such as 115-113 to 0-1;
- actions at one clock arriving out of `actionNumber` order;
- isolated, wildly high action numbers inserted among normal events;
- stale decreasing scores on support/stands replay and period-end rows.

The processor handles these without consulting the final score: within the
same period/clock it rejects an older score-bearing action, ignores
non-overturn replay snapshots, permits ordered overturn corrections, and lets a
period-end snapshot confirm/increase but not decrease reconstructed scores.
The official final score is used only afterwards as validation.
