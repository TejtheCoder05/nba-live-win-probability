# Field Map — What `PlayByPlayV3` Actually Returns

This document records the **observed** fields from `nba_api` 1.11.4, verified
against a real game (`GAME_ID 0022300061`, LAL @ DEN, 2023-10-24, 473 events),
and maps them to the implemented state and model fields.

It exists because the difference between "the API gives us this" and "we must
reconstruct this" determines how much work each feature costs. Guessing at
column names is how feature pipelines silently break.

## The 24 returned columns

```
gameId        actionNumber  clock         period        teamId       teamTricode
personId      playerName    playerNameI   xLegacy       yLegacy      shotDistance
shotResult    isFieldGoal   scoreHome     scoreAway     pointsTotal  location
description   actionType    subType       videoAvailable shotValue   actionId
```

> **Note:** `shotValue` was observed in the response from the **historical**
> `nba_api.stats.endpoints.PlayByPlayV3` endpoint, but is *missing* from that
> endpoint's `expected_data` declaration inside `nba_api` itself. The library's
> declared schema is slightly behind what the stats endpoint actually returns,
> which is a reason to inspect responses directly rather than trusting the
> package's metadata.
>
> This historical Phase 1 observation says nothing about
> `nba_api.live.nba.endpoints`. Phase 6 later inspected that separate schema
> directly and documented it in [LIVE_DATA.md](LIVE_DATA.md).

## Feature source map

| State/model field | Source field(s) | Status | Notes |
|---|---|---|---|
| home score | `scoreHome` | **direct** | string; blank except on scoring plays → forward-fill |
| away score | `scoreAway` | **direct** | same |
| score differential | `scoreHome` − `scoreAway` | **derived** | trivial once both are filled |
| period | `period` | **direct** | `int64`, 1-4 regulation, 5+ overtime |
| seconds remaining | `clock` + `period` | **derived** | parse ISO-8601 duration |
| event / action type | `actionType`, `subType` | **direct** | see caveats below |
| team responsible | `teamTricode`, `teamId`, `location` | **direct (mostly)** | blank on 36/473 events |
| home vs away side | `location` | **direct** | `'h'` / `'v'` — cleaner than tricode matching |
| shot value (2 vs 3) | `shotValue`, `isFieldGoal`, `shotResult` | **direct** | |
| team fouls this period | `description` regex, or counted | **reconstruct** | see "Team fouls" |
| timeouts remaining | `Timeout` events | **reconstruct** | team attribution is awkward |
| possession | — | **reconstruct** | no historical field exists; implemented by the shared engine |
| `home_win` label | `LeagueGameLog.PTS` + `MATCHUP` | **direct** | from final score, not from any NBA win-probability figure |

## Observed formats and their traps

### `clock` — an ISO-8601 duration, not a number

```
PT12M00.00S    PT06M07.00S    PT00M31.90S    PT00M00.00S
```

Seconds carry a fractional part. `seconds_remaining` must be **parsed**:

```
seconds_in_period = minutes * 60 + seconds
```

Total seconds remaining in the game also needs `period`, and overtime periods
are **5 minutes**, not 12 — a detail that is easy to get wrong.

### `scoreHome` / `scoreAway` — strings, blank on non-scoring plays

In the sample game, **352 of 473 events (74%)** had an empty `scoreHome`. The
score is only stamped on the event that changed it.

```python
scores = events[["scoreHome", "scoreAway"]].apply(
    lambda c: pd.to_numeric(c.replace("", pd.NA), errors="coerce")
).ffill().fillna(0)          # fillna(0): games start 0-0
```

Forgetting the forward-fill would produce a feature that is null three quarters
of the time.

### `actionType` — blank on secondary credit events

Distinct values observed, with counts:

| actionType | count |
|---|---|
| Rebound | 100 |
| Missed Shot | 92 |
| Made Shot | 89 |
| Substitution | 55 |
| Foul | 34 |
| Free Throw | 32 |
| *(blank)* | 24 |
| Turnover | 24 |
| Timeout | 12 |
| period | 8 |
| Violation | 2 |
| Jump Ball | 1 |

The 24 blank rows are **BLOCK and STEAL credit events** — supplementary rows
that credit a defender, e.g. `"James STEAL (1 STL)"`. They describe the play
only in `description`.

This matters for the possession engine: **a steal is a separate row from the
turnover it caused**, so a naive "one row = one possession change" rule would
double-count.

### `period` rows mark period boundaries exactly

`actionType == "period"` with `subType` of `start` / `end`:

```
period 1  PT12M00.00S  start   Start of 1st Period
period 1  PT00M00.00S  end     End of 1st Period
```

These give clean, unambiguous period segmentation — useful for resetting team
fouls and for the possession engine's end-of-period handling.

### `location` is a reliable home/away marker

Observed: `h` → 215 events, `v` → 222 events, blank → 8. Cross-checking against
tricodes was perfectly consistent (`h`→DEN the home team, `v`→LAL) with zero
disagreement. This is more robust than string-matching team abbreviations.

## What must be reconstructed

### Team fouls — the description already carries the count

Foul descriptions embed a running count:

```
Davis S.FOUL (P1.T1)      -> Player's 1st foul, Team's 1st foul of the period
Vincent P.FOUL (P1.T2)    -> Player's 1st, Team's 2nd
Hayes S.FOUL (P1.T3)      -> Player's 1st, Team's 3rd
Russell P.FOUL (P1.T1)    -> back to T1: the count RESETS each period
```

So `T<n>` is parseable via regex **and** independently verifiable by counting
foul events per team per period. Having two independent derivations is useful:
they can be cross-checked against each other in tests.

Caveat: technical fouls do not count toward the team-foul penalty total, so a
naive count and the `T<n>` value will legitimately diverge on technicals.

### Timeouts — events exist, but team attribution is awkward

```
period 1  PT06M07.00S  teamTricode=''  NUGGETS Timeout: Regular (Full 1 Short 0)
period 1  PT02M50.00S  teamTricode=''  Lakers Timeout: Regular (Reg.1 Short 0)
```

Two problems:

1. `teamTricode` is **blank** on timeout events — the team appears only in the
   `description`, and inconsistently cased (`NUGGETS` vs `Lakers`).
2. The trailing counter changes format mid-game (`Full 1` vs `Reg.1`).

Timeouts were therefore treated as a low-confidence candidate and excluded from
the selected V1 feature set. The project does not feed unreliable timeout
attribution into the frozen model.

### Rebounds — offensive vs defensive is NOT directly labelled

`subType` for rebounds was `Unknown` for 97 of 100 rebounds. The distinction has
to be inferred, and the obvious heuristic is **already wrong** on real data.

Comparing each rebound's team against the immediately preceding row:

```
prev: Missed Shot  LAL | rebound LAL -> OFF   correct
prev: Missed Shot  LAL | rebound DEN -> DEF   correct
prev: (blank/BLOCK) LAL | rebound LAL -> OFF  WRONG - description says Def:1
```

The third case fails because a **BLOCK credit row sits between the miss and the
rebound**, so "previous row" is not "the shot that was missed". The engine must
walk back to the last actual shot attempt, skipping credit events.

The `description` also carries the player's running totals — `(Off:1 Def:0)` —
which gives a second, independent signal to validate against.

### Possession — no field exists at all

There is no possession column, and possession does **not** simply alternate.
The Phase 3 engine handles made field goals, turnovers, defensive vs offensive
rebounds, free-throw sequences (only the last FT of a set changes possession),
missed shots, jump balls, and period boundaries.

Also note: 14 rebounds and 2 turnovers had a **blank `teamTricode`** — these are
*team* rebounds and team turnovers (shot-clock violations, balls out of bounds)
with no individual player, another case the engine must handle explicitly.

## Sources of each feature at a glance

```
LeagueGameLog  ->  which games exist, final scores, home/away  ->  home_win LABEL
PlayByPlayV3   ->  event stream                                ->  game-state FEATURES
```

The label comes from the game log and the features come from play-by-play. No
NBA-provided win-probability value is used as an input anywhere.
