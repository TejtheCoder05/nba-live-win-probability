# Docker, CI, and Railway deployment

## Production status

The verified replay application is publicly deployed on Railway:

**[https://nba-live-win-probability-production.up.railway.app](https://nba-live-win-probability-production.up.railway.app)**

Production acceptance verified:

- the Railway deployment reached `ACTIVE` and passed `/api/health`;
- `/`, the dashboard CSS, JavaScript, and vendored Socket.IO client load
  publicly;
- an external WebSocket-only Socket.IO client connected successfully;
- the authentic replay emitted 434 distinct states and ended GSW 99 – BKN 125;
- official `FINAL` produced the product display GSW 0% – BKN 100%;
- GitHub Actions passed both Python/model and production-container jobs; and
- deployment logs showed normal startup, subscriber, poller, and disconnect
  behavior without application errors.

The deployed service is intentionally in `replay` mode. Actual continuous NBA
CDN polling is outside the verified production boundary; see
[Live CDN limitation](#live-cdn-limitation).

## Production architecture

```text
Gunicorn (one gthread worker, eight threads)
  -> Flask-SocketIO + simple-websocket
  -> GameService
  -> live-format adapter + shared historical state engine
  -> frozen preprocessing + PyTorch CPU model
  -> HTTP API / Socket.IO / dashboard
```

One worker owns the in-memory game and poller registry. `simple-websocket`
provides WebSocket support to Flask-SocketIO. Multiple worker processes would
require coordinated routing and a message broker, which this single-instance
portfolio deployment does not need.

## Runtime artifact audit

Only the three frozen inference artifacts required by the application are
copied into the production image:

| File | Purpose |
|---|---|
| `best_model.pt` | 2,817 frozen PyTorch parameters |
| `model_config.json` | 10 -> 64 -> 32 -> 1 architecture |
| `preprocessing.json` | feature order, means, scales, and imputation |

Replay mode also includes the authentic `0022000001` live-format PBP and game
details fixtures. The image excludes `.git`, virtual environments, secrets,
caches, documentation, historical raw data, processed training data, manifests,
plots, evaluation outputs, and training-only artifacts.

The Docker build and CI verify the protected model hash:

```text
143ca6cadca8a86d0f47ad30f0d2a8ecaee91316b4a0071540131bcada61e591
```

## Build and run locally

```bash
docker build -t nba-win-probability:local .
docker run --rm -p 5000:5000 \
  -e NBA_APP_MODE=replay \
  nba-win-probability:local
```

Open <http://127.0.0.1:5000>. The image uses
`python:3.12.10-slim-bookworm`, the official CPU-only PyTorch 2.13.0 wheel, a
build-time frozen-model check, and non-root UID/GID 10001.

Run the full production-container acceptance check with:

```bash
python scripts/verify_container.py --image nba-win-probability:local
```

It checks dashboard and API responses, a WebSocket-only full replay, final
score and product override, model identity, lifecycle logs, non-root execution,
excluded training data, startup time, image size, and memory.

## Configuration

| Variable | Production default | Purpose |
|---|---:|---|
| `NBA_APP_MODE` | `replay` | explicit `replay` or `live` source |
| `NBA_APP_ENV` | `production` | production safety guard |
| `HOST` / `NBA_APP_HOST` | `0.0.0.0` | bind host; NBA-prefixed value wins |
| `PORT` / `NBA_APP_PORT` | `5000` | Railway-injected port supported |
| `NBA_POLL_INTERVAL_SECONDS` | `5` | conservative live request cadence |
| `NBA_REPLAY_INTERVAL_SECONDS` | `0.5` | base replay action delay |
| `NBA_REPLAY_SPEED` | `4` | replay multiplier |
| `NBA_APP_DEBUG` | `false` | rejected when true in production |
| `GUNICORN_THREADS` | `8` | threads in the single worker |
| `GUNICORN_TIMEOUT_SECONDS` | `120` | worker timeout |

No application secret is required. `/api/health` reports `status`, `mode`,
`model_loaded`, and `service_ready`. Health intentionally does not depend on NBA
CDN availability because an external feed outage should not make the process
itself unhealthy.

## Continuous integration and delivery

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`:

1. set up Python 3.12.10 with dependency caching;
2. install CPU-only PyTorch and pinned dependencies;
3. verify the frozen model SHA-256;
4. run all 166 offline tests;
5. compile `src`, `scripts`, and `tests`;
6. run `pip check` and `git diff --check`;
7. build the production image after quality checks pass;
8. verify image metadata and the non-root user; and
9. start the image and run health plus full WebSocket replay acceptance.

CI requires no NBA connection, active game, historical dataset, platform token,
or other secret. Railway builds the repository Dockerfile from `main`, binds the
application to `0.0.0.0:$PORT`, and gates readiness on `/api/health`.

`railway.json` records the Dockerfile builder, a 120-second health timeout,
graceful draining, and an on-failure restart policy. The successful first cloud
deployment also caught a schema-type issue in `drainingSeconds`; that field is
now stored as the number Railway requires and protected by a deployment test.

```text
pull request -> GitHub Actions quality + Docker acceptance
merge to main -> Railway Docker build
              -> /api/health becomes ready
              -> deployment receives HTTPS/WebSocket traffic
```

## Measured production behavior

Local Docker acceptance recorded roughly 1.24–2.06 seconds to health, a 351 MB
image, 270–465 MiB idle memory, and 358–554 MiB after replay across two runs.

Railway metrics during public acceptance showed approximately:

- 260 MB idle memory;
- 335–340 MB after replay; and
- a 0.36 vCPU peak during external replay verification.

The service returned to near-idle CPU after the replay. Resource values are
observations from the acceptance window rather than capacity guarantees.

## Live CDN limitation

The live `ScoreBoard`/`PlayByPlay` clients, schema adapter, full-response
fingerprinting, deterministic reconstruction, and model inference path are
implemented and tested with authentic captured NBA live-format responses.

However, actual continuous Railway-to-`cdn.nba.com` access has **not** been
verified. The CDN returned HTTP 403 in the original development environment,
and no active NBA game has traversed the deployed ScoreBoard -> PlayByPlay ->
adapter -> frozen model -> Socket.IO path. The project therefore makes no claim
of currently processing active NBA games.

Production ScoreBoard/CDN validation remains an optional future enhancement.
An empty scoreboard is a valid result when no games are scheduled; only a
successful request distinguishes that state from a network/CDN failure.

## Primary references

- [Flask-SocketIO deployment](https://flask-socketio.readthedocs.io/en/latest/deployment.html)
- [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles)
- [Railway config as code](https://docs.railway.com/config-as-code/reference)
- [Railway health checks](https://docs.railway.com/deployments/healthchecks)
- [Railway GitHub autodeploys](https://docs.railway.com/deployments/github-autodeploys)
- [Railway Socket.IO deployment](https://docs.railway.com/guides/socketio)
