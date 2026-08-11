# NBA Live Win Probability

A machine-learning system that learns win probability from historical NBA
play-by-play data and serves live predictions during active games.

Given the state of a game at any moment — score, time, period, possession,
fouls — the model answers one question: **what is the probability the home team
wins?**

## Project goal

Most win-probability displays are opaque. This project builds one end to end and
makes every step inspectable:

1. Pull historical games and play-by-play from the official-ish `nba_api`.
2. Turn each play-by-play event into a **game-state observation**.
3. Label every observation with whether the home team *eventually* won.
4. Train a small PyTorch model to output a calibrated probability.
5. Run the identical feature pipeline against **live** games.
6. Stream predictions to a browser dashboard over WebSockets.

The emphasis is on **probability quality**, not on picking winners. A model that
says "72%" should be right about 72% of the time.

## Architecture

```
data/
  raw/              # exact API responses (gitignored, reproducible)
    gamelogs/       #   one CSV per season game log
    playbyplay/     #   {GAME_ID}.json.gz per game (gzipped raw response)
    manifest/       #   per-season download status + failures.jsonl
  processed/        # model-ready datasets

src/
  data/             # API retrieval ONLY - no feature or model logic
    nba_client.py   #   retry / backoff / jitter / request spacing
    game_log.py     #   LeagueGameLog -> games + home_win label
    play_by_play.py #   PlayByPlayV3 -> event stream, atomic compressed writes
    validation.py   #   is a response complete and for the right game?
    manifest.py     #   machine-readable download status + failure log
    downloader.py   #   resumable bulk orchestration
  features/         # shared game-state + possession engine (Phase 3 complete)
  models/           # PyTorch model, training, evaluation    (Phase 4)
  live/             # live game polling + inference          (Phase 5)
  api/              # Flask + SocketIO server                (Phase 6)

scripts/            # runnable entry points
tests/              # pytest suite
  fixtures/         #   committed sample data so tests run offline
artifacts/          # trained weights + preprocessing artifacts (gitignored)
templates/          # dashboard HTML                          (Phase 6)
static/             # dashboard CSS + JS                      (Phase 6)
docs/
  FIELD_MAP.md      # what the API actually returns, and what we must derive
  DOWNLOADER.md     # bulk downloader architecture and recovery
```

### Why the responsibilities are split this way

**Retrieval is isolated from everything else.** `src/data/` knows how to talk to
the NBA and nothing about features or models. This means the flaky-network
concerns (retries, backoff, caching) live in exactly one place.

**Feature engineering will be shared between training and live inference.**
This is the single most important structural decision in the project. If
training computed "seconds remaining" one way and the live server computed it
another, the model would be fed inputs at serving time that do not match what it
learned — a classic training/serving skew bug that produces confidently wrong
predictions with no error message. One module, both callers.

## Planned ML pipeline

Each training row is **one moment in one game**, not one row per game:

```
GAME_ID, score differential, period, seconds remaining, possession,
team fouls, timeouts, ...   ->   home_win  (1 = home team eventually won)
```

A single game yields hundreds of rows, all sharing the same label.

### Preventing data leakage

Because every row from a game shares that game's outcome, randomly shuffling
rows into train/test would leak the answer: the model could memorise "this
game ended 121-116" from a training row and get a test row from the same game
almost free. Reported accuracy would look excellent and mean nothing.

**Splits are therefore made at the game level (and eventually by season /
chronology), never at the row level.** Every row from a given game lands
entirely in train, entirely in validation, or entirely in test.

### Model

Start with a simple baseline (e.g. logistic regression on score differential and
time remaining) so there is something to beat. Then a small MLP:

```
Input -> Linear(64) -> ReLU -> Dropout -> Linear(32) -> ReLU -> Linear(1)
```

Trained with `BCEWithLogitsLoss`; the output logit becomes a probability via
sigmoid at inference time.

### Evaluation

| Metric | Why |
|---|---|
| Log loss | Punishes confident wrong answers — the main training objective |
| Brier score | Mean squared error of the probability itself |
| Accuracy | Reported, but *not* the goal |
| Calibration / reliability curve | Do 70% predictions actually win 70% of the time? |

**No NBA-provided win-probability value is ever used as an input feature.** If
one is available, it may later serve as an external benchmark only.

## Planned live pipeline

```
nba_api.live.nba.endpoints (scoreboard + play-by-play)
        |
        v
  same feature code as training
        |
        v
   PyTorch model  ->  probability
        |
        v
  Flask + Flask-SocketIO  ->  browser dashboard
```

## Tech stack

Python 3.12 · nba_api · pandas · NumPy · PyTorch · Flask · Flask-SocketIO ·
pytest · HTML/CSS/JavaScript

No frontend framework. The dashboard is plain HTML/CSS/JS talking to a
WebSocket, which is enough for a live-updating probability chart.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Phase 1 proof of concept: pull a season, pick a game, fetch its play-by-play
python scripts/phase1_poc.py --season 2023-24

# Phase 2: bulk download. Start small to confirm things work...
python scripts/download_seasons.py --seasons 2023-24 --limit 3

# ...then run the full season (~25 min). Re-run any time to resume.
python scripts/download_seasons.py --seasons 2023-24

# What has been downloaded so far?
python scripts/download_seasons.py --seasons 2023-24 --status

# Retry only games that failed
python scripts/download_seasons.py --seasons 2023-24 --only-failed

# Run the test suite (works offline; no network calls)
python -m pytest -v

# Phase 3: rebuild and independently verify processed game states
python scripts/process_game_states.py --seasons 2021-22 2022-23 2023-24
python scripts/verify_game_states.py --seasons 2021-22 2022-23 2023-24
```

`requirements.txt` holds the Phase 1-3 runtime plus test dependencies. PyArrow
is used for typed Parquet output; PyTorch and Flask are deferred until the
phases that actually use them.

## Project status

| Phase | Scope | Status |
|---|---|---|
| 1 | Project setup + nba_api proof of concept | ✅ Complete |
| 2 | Bulk multi-season downloader (cache, resume, retries) | ✅ Complete (three seasons) |
| 3 | Feature engineering + possession engine | ✅ Complete (three seasons) |
| 4 | Baseline model, PyTorch MLP, evaluation | ⬜ Not started |
| 5 | Live game polling + inference | ⬜ Not started |
| 6 | Flask + SocketIO + dashboard | ⬜ Not started |

### What Phase 1 established

- Retrieval works against `nba_api` 1.11.4 for both `LeagueGameLog` and
  `PlayByPlayV3`.
- The 2023-24 regular season returns **2,460 rows for 1,230 games** — the game
  log is team-level, so GAME_ID deduplication is mandatory.
- Play-by-play carries usable period, clock, scoring, and event information.
- Possession, team fouls, and timeouts are **not** provided as fields and must
  be reconstructed. See [docs/FIELD_MAP.md](docs/FIELD_MAP.md) for the full
  evidence-backed breakdown.

### What Phase 2 adds

A resumable, validating bulk downloader — see
[docs/DOWNLOADER.md](docs/DOWNLOADER.md) for the full design.

The central idea: **a game counts as downloaded only when its saved response has
been validated**, never merely because a file exists. A truncated or wrong-game
response is silent corruption that would surface much later as inexplicable gaps
in training data, so files are written atomically and re-validated on every
resume.

Supporting that: a per-season JSON manifest (`pending` / `downloaded` /
`failed`), an append-only failure log, gzipped raw storage (~20 MB per season
instead of ~400 MB), sequential requests with deliberate spacing, and no
concurrency. Reliability over speed, deliberately.

**Data on hand:** the full 2023-24 regular season — 1,230 games, 598,705
play-by-play events, 20.4 MB on disk, downloaded in ~20 minutes with **zero
failures**, independently re-verified game by game via
`scripts/verify_downloads.py`.

### What Phase 3 adds

A leakage-safe, stateful historical feature engine plus 3,690 independently
rebuildable Parquet game partitions. Across 2021-22 through 2023-24 it turns
1,803,208 raw events into 1,437,879 meaningful states, validates every final
score, and reconstructs known possession for 93.57% of states without guessing
when raw team ownership is absent. See [docs/FEATURE_ENGINEERING.md](docs/FEATURE_ENGINEERING.md),
[docs/EVENT_SCHEMA.md](docs/EVENT_SCHEMA.md), and
[docs/PHASE3_REPORT.md](docs/PHASE3_REPORT.md).
