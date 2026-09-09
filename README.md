# NBA Live Win Probability

A deployed, end-to-end NBA win-probability application built from historical
play-by-play data. It reconstructs the state of a game after each meaningful
event, scores that state with a frozen PyTorch model, and streams the result to
an interactive browser dashboard over Socket.IO.

**Live demo:** [https://nba-live-win-probability-production.up.railway.app](https://nba-live-win-probability-production.up.railway.app)

The public demo runs a verified replay of authentic NBA live-format data. It is
not presented as an active-game feed; see [Current limitation](#current-limitation).

## Verified results

| Area | Result |
|---|---:|
| Historical coverage | 3 NBA regular seasons |
| Games | 3,690 |
| Reconstructed game states | 1,437,879 (1.44M+) |
| PyTorch MLP Brier score | 0.163 |
| PyTorch MLP log loss | 0.483 |
| PyTorch MLP ROC-AUC | 0.837 |
| Median single-state model inference | 0.316 ms |
| Median backend state processing | 19.372 ms |
| Authentic live-format replay states | 434 |
| Automated tests | 166 |

Evaluation uses a held-out chronological season: train on 2021-22, select on
2022-23, and test once on 2023-24. The raw PyTorch MLP outperformed the logistic
baseline (Brier 0.168, log loss 0.501). Platt scaling was evaluated and rejected
because it worsened validation probability quality, so the deployed model is
intentionally the uncalibrated frozen MLP.

## Why this project

Win-probability systems combine several engineering problems that are easy to
hide behind one percentage: reliable data collection, stateful sports logic,
leakage-safe evaluation, training/serving consistency, low-latency inference,
and streamed application delivery. This project makes that entire path
inspectable and keeps one canonical state engine shared by historical
processing, live-format replay, and the serving layer.

The target is probability quality rather than winner classification. A useful
model should distinguish a 55% situation from a 95% situation and be evaluated
with scoring rules that punish confident mistakes.

## How it works

```text
NBA play-by-play
  -> game-state reconstruction
  -> frozen PyTorch win-probability model
  -> Flask backend
  -> Socket.IO / WebSockets
  -> interactive dashboard
```

```text
Historical APIs                    Authentic live-format replay
      |                                      |
      v                                      v
source-specific normalization -> canonical event contract
                                      |
                                      v
                    shared score / clock / possession /
                          foul reconstruction engine
                                      |
                                      v
                        frozen 10-feature preprocessor
                                      |
                                      v
                            PyTorch MLP inference
                                      |
                                      v
                    Flask API + Socket.IO + dashboard
```

Source adapters isolate API schema differences before data reaches the shared
basketball logic. This prevents a second serving-only feature implementation
from drifting away from the definitions used during model training.

## Model and features

Each training example represents one meaningful moment in a game and is labeled
with whether the home team eventually won. The frozen model uses exactly these
ten features, in this order:

1. `score_differential`
2. `period`
3. `seconds_remaining_period`
4. `seconds_remaining_regulation`
5. `is_overtime`
6. `overtime_number`
7. `home_possession`
8. `possession_known`
9. `home_team_fouls_period`
10. `away_team_fouls_period`

In plain language, the signal comes from score differential, period and time
remaining, overtime state, possession, an explicit possession-known flag, and
team fouls. Unknown possession is never guessed: it remains
`home_possession=0.5` with `possession_known=0`.

The selected network is a small 2,817-parameter MLP:

```text
10 inputs -> Linear(64) -> ReLU -> Dropout
          -> Linear(32) -> ReLU -> Linear(1) -> sigmoid
```

Preprocessing statistics, feature order, architecture, and weights are saved as
versioned inference artifacts. CI protects the model with SHA-256:

```text
143ca6cadca8a86d0f47ad30f0d2a8ecaee91316b4a0071540131bcada61e591
```

No NBA-provided win-probability value is used as an input feature.

## Data and evaluation design

The historical pipeline downloaded and independently validated three regular
seasons (2021-22 through 2023-24), covering 3,690 games and 1,803,208 raw
events. The state engine emitted 1,437,879 model-ready observations and matched
every final score.

Splits occur by complete season and game, never by randomly shuffled event row.
That prevents moments from the same game appearing in both training and test
sets and avoids a subtle but severe form of outcome leakage. Preprocessing is
fit on the training season only.

Raw downloads and processed training datasets are intentionally excluded from
Git. Small, authentic fixtures keep the 166-test suite deterministic and fully
offline.

## Dashboard and replay

The responsive vanilla HTML/CSS/JavaScript dashboard displays:

- teams, score, period, clock, and game status;
- home/away probability split and trajectory;
- possession and possession-known state;
- period team fouls and recent play descriptions; and
- replay controls for play, pause, step, reset, and speed.

Replay mode uses an authentic 610-action NBA live-format capture for game
`0022000001` rather than fabricated browser data. The full adapter -> state
engine -> frozen model -> Socket.IO -> UI path produces 434 distinct states and
finishes Golden State 99, Brooklyn 125.

The raw model result remains untouched at the end of the game. Only after the
source reports official `FINAL` status does the product layer display 0% for
the loser and 100% for the winner. This keeps future information out of the
historical model while giving the UI correct terminal certainty.

## Production and CI/CD

The public application is built from the repository Dockerfile and deployed on
Railway from `main`:

- Python 3.12 slim production image;
- CPU-only PyTorch;
- non-root runtime user;
- Gunicorn with one threaded worker;
- Flask-SocketIO with `simple-websocket`;
- `0.0.0.0:$PORT` platform binding;
- `/api/health` Railway health check; and
- a Railway-provided HTTPS domain with WebSocket support.

GitHub Actions runs on pull requests and pushes to `main`. It verifies the
frozen model hash, executes the complete offline test suite, compiles Python
sources, checks dependency integrity, builds the production image, and runs a
containerized health and WebSocket replay acceptance test.

## Run locally

Create the pinned environment and start the verified replay:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_app.py --mode replay
```

Open <http://127.0.0.1:5000>.

Run the offline verification suite:

```bash
python -m pytest -q
python scripts/verify_model_artifact.py
python -m compileall -q src scripts tests
python -m pip check
```

Build and verify the production container:

```bash
docker build -t nba-win-probability:local .
python scripts/verify_container.py --image nba-win-probability:local
```

Historical downloads are reproducible through the scripts in `scripts/`, but
they are not required to run the demo or tests. See
[docs/DOWNLOADER.md](docs/DOWNLOADER.md) for the resumable download workflow.

## Repository layout

```text
src/data/       historical retrieval, validation, manifests, and caching
src/features/   canonical event and state reconstruction
src/models/     chronological splits, preprocessing, MLP, and inference
src/live/       ScoreBoard/PBP clients, live adapter, replay, and pollers
src/api/        Flask application, routes, Socket.IO events, and Gunicorn entry
scripts/        data, modeling, verification, benchmark, and app entry points
tests/          offline unit, integration, parity, and deployment tests
artifacts/      allowlisted frozen serving artifacts
static/         dashboard JavaScript, CSS, and vendored Socket.IO client
templates/      dashboard HTML
docs/           design decisions, schemas, phase reports, and deployment guide
```

## Current limitation

Replay mode is fully verified locally, in Docker, in GitHub Actions, and through
the public Railway deployment. The `nba_api.live` ScoreBoard/PlayByPlay client,
canonical adapter, fingerprint-based reconstruction, and failure handling are
implemented and tested with authentic captured responses.

Actual continuous Railway-to-`cdn.nba.com` polling has **not** been verified.
The NBA CDN returned HTTP 403 from the original development environment, and no
active NBA game has yet completed the production ScoreBoard -> PlayByPlay ->
model -> WebSocket path. Accordingly, this repository does **not** claim that
it currently processes active NBA games.

That cloud CDN validation is a future enhancement, not a dependency of the
verified portfolio demo. The accurate description today is: **a deployed NBA
win-probability application with a verified live-format replay pipeline and an
architecture designed for live NBA ingestion.**

## Documentation

- [Feature engineering](docs/FEATURE_ENGINEERING.md)
- [Modeling and evaluation](docs/MODELING.md)
- [Live ingestion and parity](docs/LIVE_DATA.md)
- [Application and Socket.IO API](docs/APPLICATION.md)
- [Docker, CI, and Railway](docs/DEPLOYMENT.md)
- [Final Phase 8 verification report](docs/PHASE8_REPORT.md)
