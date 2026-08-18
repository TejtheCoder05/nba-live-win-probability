# Phase 8 verification report

## Current status

Docker containerization and local production verification are complete. GitHub
Actions is configured but has not yet run remotely. Railway configuration is
prepared, but deployment is deliberately paused until GitHub Actions passes.

## Docker

- Base: `python:3.12.10-slim-bookworm`.
- Server: Gunicorn 26.0.0, one `gthread` worker, eight threads, and
  `simple-websocket`.
- PyTorch: exact 2.13.0 CPU wheel from the official CPU index; no CUDA runtime.
- Runtime user: non-root `app` (UID/GID 10001).
- Frozen model: verified during build and inside the running container.
- Included: application, frontend, three inference artifacts, and two authentic
  replay fixtures.
- Excluded: repository metadata, environments, secrets, caches, documentation,
  historical/training datasets, and training-only artifacts.

Verified local results on Docker Desktop ARM64:

| Check | Result |
|---|---:|
| Image size | 351,080,307 bytes (about 351 MB / 335 MiB) |
| Health-ready startup | 1.240–2.060 seconds |
| Idle memory | about 270–465 MiB |
| Post-replay memory | about 358–554 MiB |
| WebSocket updates | 436 |
| Distinct sequences | 434 |
| Final score | GSW 99 – BKN 125 |
| FINAL display | GSW 0% – BKN 100% |
| Raw final home probability | 0.9992812928 |
| Production lifecycle logs | verified |

The acceptance client used WebSocket-only Socket.IO against the real Gunicorn
container. It verified `/`, `/api/health`, `/api/games`, authentic game ID,
internal model hash, non-root execution, logs, and excluded runtime paths.

## CI and model integrity

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`. Its quality
job installs pinned CPU dependencies, checks the frozen hash, runs pytest,
compiles sources, checks dependencies, and checks whitespace. A dependent job
builds the image and runs the full containerized WebSocket replay acceptance.

Local final verification before the infrastructure commit:

- 166 tests passed;
- compilation passed;
- `pip check` passed;
- `git diff --check` passed;
- workflow YAML and `railway.json` parsed;
- Docker build and production replay passed; and
- model SHA-256 passed inside and outside the container.

The three production artifacts are explicitly allowlisted for source control;
evaluation and training artifacts remain ignored. Remote CI is not claimed until
the GitHub check suite completes.

## CD and cloud

`railway.json` selects the repository Dockerfile, `/api/health`, deployment
timeout, graceful draining, and on-failure restarts. Actual CD remains disabled:
after CI passes, the repository must be connected to Railway and **Wait for CI**
must be enabled before deployment.

The measured 554 MiB cold high-water result exceeds Railway's post-trial Free
limit of 0.5 GB. A higher-limit usage-metered plan requires explicit user billing
authorization. No Railway project, service, domain, or paid resource has been
created.

Still unverified:

- public URL and cloud health;
- external cloud WebSocket replay;
- cloud startup/restart behavior and resource use;
- cloud logs; and
- cloud `cdn.nba.com`, ScoreBoard, or active-game PlayByPlay access.

The local NBA CDN HTTP 403 limitation remains explicit. Replay, schema, semantic
parity, and inference are verified; continuous network polling of an actually
active NBA game is not.

## Frozen model

- SHA-256:
  `143ca6cadca8a86d0f47ad30f0d2a8ecaee91316b4a0071540131bcada61e591`.
- Architecture: unchanged 10 → 64 → 32 → 1.
- Feature order and unknown-possession semantics: unchanged.
- No retraining, tuning, calibration, preprocessing, weight, feature, adapter,
  possession, foul, or historical-pipeline changes occurred.

The Phase 7 latency baseline remains 19.372 ms median and 28.351 ms p95. No
serving-path feature or inference algorithm changed.

## Interview explanation

1. Docker fixes the OS, Python, CPU PyTorch, server, code, artifacts, and command.
2. The image also guarantees training data and evaluation outputs are absent.
3. CI checks tests, compilation, dependencies, whitespace, model identity, the
   image build, and a real containerized WebSocket replay.
4. CI validates a revision; CD promotes a passing `main` revision.
5. Railway's **Wait for CI** setting prevents a failed check suite from deploying.
6. WebSockets require upgrade-capable routing and a compatible worker topology.
7. Training data is excluded because serving needs only frozen inference inputs.
8. Host, image-build, and running-container checks protect the model hash.
9. Environment variables separate source mode and runtime settings from code.
10. Only an actual cloud ScoreBoard call can resolve the local CDN 403 limitation.
11. The path is push → GitHub quality → Docker acceptance → Railway health gate.
12. Kubernetes would add complexity without solving a present scaling need.
