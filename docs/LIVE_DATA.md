# Live NBA ingestion and feature parity

## Scope

Phase 6 proves this path without building an application server:

```text
nba_api.live response
        -> source-specific live adapter
        -> canonical historical-shaped action contract
        -> shared Phase 3 score/clock/possession/foul state engine
        -> exact frozen ten-feature vector
        -> frozen WinProbabilityPredictor
```

There is no Flask, Socket.IO, WebSocket, frontend, polling loop, or deployment
code in this phase.

## Installed interfaces inspected

The installed package is `nba_api==1.11.4`.

- `nba_api.live.nba.endpoints.scoreboard.ScoreBoard(proxy=None, headers=None,
  timeout=30, get_request=True)` requests
  `scoreboard/todaysScoreboard_00.json`.
- `nba_api.live.nba.endpoints.playbyplay.PlayByPlay(game_id, proxy=None,
  headers=None, timeout=30, get_request=True)` requests
  `playbyplay/playbyplay_{game_id}.json`.

Both use `https://cdn.nba.com/static/json/liveData/{endpoint}` and expose the
decoded response via `get_dict()`. `ScoreBoard.games` and `PlayByPlay.actions`
are thin `Endpoint.DataSet` wrappers around the corresponding lists.

The production wrappers handle endpoint failure without crashing. When the
scoreboard cannot be decoded, current game availability is not established and
is never falsely reported as “zero games.” Empty scoreboards are supported and
tested independently.

## Live CDN request headers

`nba_api` 1.11.4 sends a default header set (`STATS_HEADERS`) whose user-agent
is a 2020-era Chrome 87 build and which omits `Origin` and `Referer`. The Akamai
edge in front of `cdn.nba.com` now answers that set with HTTP 403 Access Denied.
This is what produced the 403 recorded in the Phase 6 and Phase 7 reports; it is
a header-fingerprint rejection, not a network, IP, or geographic block.

`src/live/headers.py` defines the verified replacement set and both live clients
pass it through the existing `endpoint_factory` seam into the official
`headers=` constructor parameter:

```python
from src.live.headers import live_request_headers

endpoint_factory(timeout=timeout, headers=live_request_headers())
```

The set was verified from inside the deployed Railway container, interleaved
against the same edge in the same instants so the two differed only by headers:

| Header set | Result |
|---|---:|
| `nba_api` `STATS_HEADERS` default | HTTP 403 |
| `LIVE_REQUEST_HEADERS` | HTTP 200 |

`ScoreBoard(headers=LIVE_REQUEST_HEADERS)` succeeded repeatedly and returned a
decoded payload. The set is verified as a group; individual members are not
independently load-bearing, so it is passed whole rather than trimmed.
`live_request_headers()` returns a fresh copy per call so an endpoint cannot
mutate the shared constant.

The offline fixtures are authentic public captures pinned to repository
commits, not schemas synthesized from package examples. Their provenance is in
`tests/fixtures/live/README.md`. A direct historical PlayByPlayV3 request for
the same captured live game succeeded, allowing a real same-game comparison.

## Observed scoreboard schema

The captured scoreboard has `meta` and `scoreboard`; `scoreboard` contains
`gameDate`, `leagueId`, `leagueName`, and `games`. Observed game fields are:

```text
gameId, gameCode, gameStatus, gameStatusText, period, gameClock,
gameTimeUTC, gameEt, regulationPeriods, seriesGameNumber, seriesText,
ifNecessary, homeTeam, awayTeam, gameLeaders, teamLeaders, broadcasters
```

Observed team fields include:

```text
teamId, teamName, teamCity, teamTricode, teamSlug, wins, losses, score,
seed, inBonus, timeoutsRemaining, periods
```

`LiveGame` normalizes only stable fields needed downstream: game ID/status,
status text, period, clock, and oriented home/away team identity and score.
Missing optional text becomes empty, missing numeric values remain `None`, and
missing team objects do not crash normalization. NBA status values 1/2/3 map to
scheduled/active/final helpers; halftime remains active with its status text.

## Observed live PlayByPlay schema

The real `0022000001` fixture contains 610 actions under `game.actions`. Across
those actions the observed fields are:

```text
actionNumber, actionType, subType, descriptor, qualifiers, orderNumber,
clock, period, periodType, timeActual, edited, teamId, teamTricode,
personId, playerName, playerNameI, personIdsFilter, possession,
scoreHome, scoreAway, shotResult, isFieldGoal, shotDistance,
shotActionNumber, pointsTotal, x, y, xLegacy, yLegacy, side,
assistPersonId, assistPlayerNameInitial, assistTotal,
blockPersonId, blockPlayerName, stealPersonId, stealPlayerName,
reboundOffensiveTotal, reboundDefensiveTotal, reboundTotal,
turnoverTotal, foulDrawnPersonId, foulDrawnPlayerName,
foulPersonalTotal, foulTechnicalTotal, officialId,
jumpBallWonPersonId, jumpBallWonPlayerName,
jumpBallLostPersonId, jumpBallLostPlayerName,
jumpBallRecoverdPersonId, jumpBallRecoveredName, description
```

Observed live action types were `2pt`, `3pt`, `rebound`, `freethrow`, `foul`,
`turnover`, `jumpball`, `period`, `substitution`, `timeout`, `violation`,
`steal`, `block`, `stoppage`, and `game`.

Historical V3 instead directly labels `Made Shot`, `Missed Shot`, `Free Throw`,
`Jump Ball`, and similar semantic categories. It carries `actionId`,
`shotValue`, `location`, and `videoAvailable`, but lacks many of the richer live
descriptor, qualifier, participant, running-total, `possession`, and
`orderNumber` fields.

## Adapter contract

`CanonicalLiveEvent` contains source identity/order, period/clock, team,
canonical action/subtype, description, score snapshots, shot result, and the
original live action labels. `as_processor_action()` produces the same mapping
already consumed by Phase 3.

Key mappings are:

| Live | Shared category |
|---|---|
| `2pt` / `3pt` + made result | `Made Shot` |
| `2pt` / `3pt` + missed result | `Missed Shot` |
| `freethrow` | `Free Throw` with sequence retained |
| `rebound` | `Rebound` |
| `turnover` | `Turnover` |
| `foul` + subtype/descriptor | historical foul subtype semantics |
| `jumpball` | `Jump Ball` |
| `period` | `period` |
| block/steal/display/stoppage/game rows | metadata/credit rows |

Live foul `subType=personal, descriptor=shooting` becomes `Shooting`; loose-ball
and offensive/charge descriptions are mapped equivalently. This preserves the
historical definition of which fouls advance the period team-foul counter.

`orderNumber`, which is monotonic in the real fixture, controls event order;
`actionNumber` is not monotonic after edits. Repeated source identities retain
the latest version. Unknown future action types become inert metadata while
preserving their original label for diagnosis.

The live feed's `possession` field is retained at the adapter boundary but not
used to silently replace the trained meaning. The same conservative possession
engine processes both sources. Jump balls remain unknown, and any case lacking
enough historical-equivalent evidence stays unknown rather than being guessed.

## Replay and repeated updates

`LiveReplayEngine` uses a correctness-first full reconstruction. Each response
is fingerprinted. If a repeated poll is byte-semantically identical, the prior
state is returned with `changed=False`; no event can be counted twice. If any
event is added, removed, or corrected, the fingerprint changes and the complete
current feed is rebuilt in `orderNumber` order. This safely handles revisions
without assuming an append-only feed. It is fast enough for one basketball game
and avoids an unsafe incremental state cache.

Raw development caches use `data/live/playbyplay/`, separate from immutable
historical `data/raw/playbyplay/` and processed training Parquet. Writes are
atomic. The committed fixtures live only under `tests/fixtures/live/`.

## Frozen feature output

The live model vector is checked against the saved artifact order:

```text
score_differential, period, seconds_remaining_period,
seconds_remaining_regulation, is_overtime, overtime_number,
home_possession, possession_known, home_team_fouls_period,
away_team_fouls_period
```

Home-minus-away orientation is explicit. Known home possession is 1.0, away is
0.0, and unknown is exactly `home_possession=0.5`, `possession_known=0` at the
model boundary. The shared historical clock parser supplies seconds, regulation
time, overtime flag, and overtime number. No timeout or new model feature is
introduced.

Example from real live-format game `0022000001`, Q2 7:48, Brooklyn home:

```python
{
    "score_differential": 18,
    "period": 2,
    "seconds_remaining_period": 468.0,
    "seconds_remaining_regulation": 1908.0,
    "is_overtime": 0,
    "overtime_number": 0,
    "home_possession": 1.0,
    "possession_known": 1,
    "home_team_fouls_period": 1,
    "away_team_fouls_period": 3,
}
```

The unchanged frozen predictor returns `P(home win)=0.916364` for that state.

## Usage

Offline replay with sampled state/probability output:

```bash
python scripts/replay_live_game.py
```

Offline historical/live parity verification:

```bash
python scripts/verify_live_parity.py
```

One-shot production interfaces (neither starts a loop):

```python
from src.live.scoreboard import fetch_current_scoreboard
from src.live.play_by_play import fetch_live_play_by_play

board = fetch_current_scoreboard()
snapshot = fetch_live_play_by_play(game_id)
```

## Serving status and final-game behavior

The replay CLI never modifies model probabilities. The Phase 7 serving layer
consumes `ScoreboardSnapshot`, passes changed PBP snapshots into the shared
reconstruction and inference path, and publishes raw-model probability through
Socket.IO. When official `gameStatus == 3`, product logic displays 100% for the
official winner and 0% for the loser. That is an after-final display rule, not
a training feature or model change.

Phase 8 publicly deployed and verified this path in replay mode.

Railway-to-`cdn.nba.com` connectivity is verified: `ScoreBoard` succeeds from
inside the deployed container with the corrected headers. Active-game ingestion
remains unverified because the successful scoreboard request occurred during the
NBA offseason and correctly returned zero games, so no live `PlayByPlay` stream
existed to drive the path. Active-game ingestion is therefore not claimed.
