# Docker, CI, and Railway deployment

## Production architecture

```text
Gunicorn (one gthread worker)
  -> Flask-SocketIO + simple-websocket
  -> GameService
  -> Phase 6 adapter + shared state engine
  -> frozen preprocessing + PyTorch CPU model
  -> HTTP API / Socket.IO / dashboard
```

Gunicorn uses one worker process and eight threads. `simple-websocket` supplies
WebSocket support. Multiple Socket.IO worker processes would require coordinated
connection routing and a message broker, neither of which this single-instance
portfolio application needs. Local development continues to use
`scripts/run_app.py`.

## Runtime artifact audit

Only three frozen inference artifacts are copied:

| File | Purpose |
|---|---|
| `best_model.pt` | 2,817 frozen PyTorch parameters |
| `model_config.json` | 10 → 64 → 32 → 1 architecture |
| `preprocessing.json` | frozen feature order, means, scales, and imputation |

Replay mode also includes the authentic `0022000001` live-format PBP and game
details. Source, templates, frontend assets, and the vendored Socket.IO client
complete the runtime inputs.

The image excludes `.git`, virtual environments, secrets, tests, caches, docs,
historical raw data, processed training data, manifests, plots, metrics, and
training-only artifacts. The three-season datasets occupy about 182 MB locally
but are unnecessary for inference. The Docker build context is about 700 KB
before dependencies.

## Build and run

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

It verifies the dashboard, health and game APIs, WebSocket-only full replay,
99–125 final score, 0%/100% official FINAL display, raw model output, internal
model hash, lifecycle logs, non-root execution, excluded training data, startup
time, image size, and memory.

## Configuration

| Variable | Production default | Purpose |
|---|---:|---|
| `NBA_APP_MODE` | `replay` | explicit `replay` or `live` source |
| `NBA_APP_ENV` | `production` | production safety guard |
| `HOST` / `NBA_APP_HOST` | `0.0.0.0` | bind host; NBA-prefixed value wins |
| `PORT` / `NBA_APP_PORT` | `5000` | platform-injected port supported |
| `NBA_POLL_INTERVAL_SECONDS` | `5` | conservative live request cadence |
| `NBA_REPLAY_INTERVAL_SECONDS` | `0.5` | base replay action delay |
| `NBA_REPLAY_SPEED` | `4` | replay multiplier |
| `NBA_APP_DEBUG` | `false` | rejected when true in production |
| `GUNICORN_THREADS` | `8` | threads in the single worker |
| `GUNICORN_TIMEOUT_SECONDS` | `120` | worker timeout |

No secret is required. `/api/health` reports `status`, `mode`, `model_loaded`,
and `service_ready`. Health does not depend on NBA CDN availability because the
application can be healthy while an external feed is degraded.

## Continuous integration

`.github/workflows/ci.yml` runs for pull requests and pushes to `main`:

1. set up Python 3.12.10 with dependency caching;
2. install CPU-only PyTorch and pinned dependencies;
3. verify the frozen model SHA-256;
4. run all offline tests;
5. compile `src` and `scripts`;
6. run `pip check` and `git diff --check`;
7. build the production image after quality checks pass;
8. verify the image's non-root user; and
9. start the image and run the health + WebSocket replay acceptance check.

CI needs no NBA connection, active game, historical download, platform token,
or other secret. The protected model hash is:

```text
143ca6cadca8a86d0f47ad30f0d2a8ecaee91316b4a0071540131bcada61e591
```

## Railway deployment and CD

Railway was selected because it detects repository Dockerfiles, injects `PORT`,
supports health checks and long-lived WebSockets, and integrates with GitHub.
`railway.json` explicitly selects the Dockerfile, `/api/health`, a 120-second
health timeout, graceful draining, and an on-failure restart policy.

Deployment is intentionally not active yet. After GitHub Actions passes:

1. create a Railway project from this GitHub repository;
2. select branch `main` and keep a single replica;
3. enable **Wait for CI** in service settings so failed checks skip deployment;
4. confirm replay mode (`NBA_APP_MODE=replay`) and production mode
   (`NBA_APP_ENV=production`); and
5. generate a public Railway domain.

Railway's **Wait for CI** switch is a service/GitHub integration setting, not a
field in `railway.json`. The intended flow is:

```text
pull request -> GitHub Actions quality + Docker acceptance
push main -> GitHub Actions passes
          -> Railway Docker build
          -> /api/health becomes ready
          -> deployment receives traffic
```

Two local runs used 270–465 MiB idle and 358–554 MiB after replay. The current
Railway Free ceiling is 0.5 GB after trial, so it has insufficient high-water
headroom. Hobby is usage-metered, has higher limits, and requires the user's
billing authorization. No plan or paid service is created by this repository.

Primary sources:

- [Flask-SocketIO deployment](https://flask-socketio.readthedocs.io/en/latest/deployment.html)
- [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles)
- [Railway config as code](https://docs.railway.com/config-as-code/reference)
- [Railway health checks](https://docs.railway.com/deployments/healthchecks)
- [Railway GitHub autodeploys and Wait for CI](https://docs.railway.com/deployments/github-autodeploys)
- [Railway Socket.IO deployment](https://docs.railway.com/guides/socketio)
- [Railway pricing](https://railway.com/pricing)

## Cloud acceptance checklist

After the first authorized deployment:

1. record the public `up.railway.app` URL and cold-start duration;
2. verify `/`, `/api/health`, `/api/games`, and game state externally;
3. connect with an external WebSocket-only Socket.IO client;
4. run the replay to 99–125 and verify GSW 0% / BKN 100%;
5. inspect startup, poller, disconnect, and FINAL-override logs;
6. restart the service and repeat health/WebSocket checks; and
7. test `ScoreBoard` from the cloud, recording whether `cdn.nba.com` returns
   HTTP 403, then test PlayByPlay only if an active game exists.

Do not claim live end-to-end behavior until a genuinely active game has passed
through the cloud adapter, frozen model, Socket.IO connection, and dashboard.
