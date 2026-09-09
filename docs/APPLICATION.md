# Phase 7 application

## Architecture

The application is a thin serving layer over the frozen Phase 6 path:

```text
ScoreBoard -> game discovery -> server-side PlayByPlay poller
    -> Phase 6 live adapter -> shared historical state engine
    -> frozen preprocessing + PyTorch predictor
    -> application JSON state -> Flask API + Socket.IO -> browser
```

Flask routes do not contain basketball or model logic. `GameService` owns game
discovery, replay state, polling, inference results, and JSON-safe application
state. `PollerRegistry` reference-counts viewers, so every subscribed game has
at most one background loop. Flask-SocketIO uses its threading backend with
`simple-websocket`; the browser Socket.IO client is vendored for offline demo
use.

## Run locally

Install the pinned environment, then start the authentic replay:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_app.py --mode replay
```

Open <http://127.0.0.1:5000>. The replay is game `0022000001`, Golden State at
Brooklyn, sourced from the authentic live-format fixture committed during
Phase 6. It progressively passes through the real adapter, state engine,
preprocessor, frozen model, and Socket.IO path; it is not a frontend simulation.

The same verified replay is publicly deployed at
[https://nba-live-win-probability-production.up.railway.app](https://nba-live-win-probability-production.up.railway.app).

Live mode is separate:

```bash
python scripts/run_app.py --mode live
```

`cdn.nba.com` is reachable from the deployed Railway container and `ScoreBoard`
succeeds there with the corrected request headers in `src/live/headers.py`.
Environments that the NBA edge still rejects report `Live NBA feed unavailable
from this environment.` without a 500 or raw traceback, and replay mode remains
the reliable local demo. Continuous polling of an actually active NBA game has
not yet been verified, because connectivity was confirmed during the offseason
when no game was in progress.

## Configuration

All settings have replay-safe defaults and may be supplied as environment
variables. See `.env.example`.

| Variable | Default | Meaning |
|---|---:|---|
| `NBA_APP_MODE` | `replay` | `replay` or `live` |
| `NBA_POLL_INTERVAL_SECONDS` | `5` | live PBP cadence; values below 2 are rejected |
| `NBA_REPLAY_INTERVAL_SECONDS` | `0.5` | base delay per source action |
| `NBA_REPLAY_SPEED` | `4` | initial replay multiplier, 0.25–20 |
| `NBA_APP_HOST` | `127.0.0.1` | development bind host |
| `NBA_APP_PORT` | `5000` | development port |
| `NBA_APP_DEBUG` | `false` | Flask debug mode |

No secret is required or committed.

## HTTP API

| Method and route | Result |
|---|---|
| `GET /` | dashboard |
| `GET /api/health` | process status and mode |
| `GET /api/games` | normalized available games plus availability/error status |
| `GET /api/games/<game_id>` | latest JSON-safe application state |
| `POST /api/replay/<game_id>/<action>` | replay `start`, `pause`, `resume`, `reset`, `step`, or `speed` |

Game IDs must be ten digits. Invalid IDs return JSON 400; unknown valid IDs
return JSON 404. An unavailable live feed returns HTTP 200 with
`available=false`, a friendly error, and the last known games if any. A
temporary PBP failure preserves the last valid state, marks it degraded, and is
retried by the next background iteration.

The game-state response includes oriented teams/scores, period and clock,
score differential, known/unknown possession, period foul counts, both
probabilities, bounded probability history, bounded recent events, source and
connection status, timestamp, and replay controls. Only Python JSON primitives
cross the API boundary.

## Socket.IO contract and polling lifecycle

Client events:

- `subscribe_game {game_id}` validates and joins the game room, sends the
  current state, and starts or reuses its poller.
- `unsubscribe_game {game_id}` leaves the room and stops work when the final
  viewer leaves.
- `replay_control {game_id, action, speed?, steps?}` drives the same backend
  replay used by the HTTP controls.

Server events:

- `game_state` contains the complete latest application state.
- `game_status` reports connection, subscription, and replay-control status.
- `game_error` contains a safe user-facing message.

The live client fingerprints every full response. An identical response returns
the cached replay result and produces no duplicate state broadcast. Changed or
corrected responses are fully reconstructed, so revisions are deterministic and
cannot double-count. Replay metadata rows that do not emit a shared game state
also do not cause a redundant `game_state` broadcast. Probability and recent
event histories are bounded to 240 and 12 entries by default.

Background loops use Flask-SocketIO tasks and sleeps, not HTTP request threads.
The final unsubscribe/disconnect signals the loop's stop event. Tests disable
task creation while exercising the same service synchronously.

## Model and official final status

The predictor is loaded once at application startup from the frozen V1
artifacts. The exact ten-feature input order remains:

```text
score_differential, period, seconds_remaining_period,
seconds_remaining_regulation, is_overtime, overtime_number,
home_possession, possession_known, home_team_fouls_period,
away_team_fouls_period
```

Unknown possession remains `home_possession=0.5` with
`possession_known=0`. The browser never computes a prediction.

`model_home_win_probability` always retains the frozen model result. Only when
the official game status equals FINAL (`gameStatus == 3`) does the product
display 1.0 for the official winner and 0.0 for the loser. A non-final game at
0:00 is not overridden. This display rule is deliberately outside inference
and introduces no future information into training or historical evaluation.

## Dashboard and validation

The single responsive page uses vanilla HTML, CSS, JavaScript, and canvas. It
shows teams, score, quarter/overtime, clock/status, animated probability split,
bounded probability history with a 50% reference, recent plays, possession,
fouls, source/mode/connection indicators, and replay controls. Team text and
tricodes are used; no copyrighted logos are bundled.

Deterministic validation:

```bash
python -m pytest -q
python -m compileall -q src scripts tests
python -m pip check
python scripts/benchmark_application.py --samples 100
```

For manual validation, run replay mode, open the page at desktop and narrow
width, confirm the connection becomes green, press Play, and observe score,
clock, probability bar/chart, and recent plays change. Pause, Step, speed, and
Reset should update without console errors. The automated Flask and Socket.IO
tests cover those backend contracts even when browser automation is unavailable.
