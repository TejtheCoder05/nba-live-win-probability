# Phase 8 verification report

## Final portfolio status

Phase 8 is complete for its verified scope: Docker production packaging,
GitHub Actions, Railway deployment, public HTTP/static delivery, public
Socket.IO/WebSocket replay, resource observation, and frozen-model protection.

Public application:
[https://nba-live-win-probability-production.up.railway.app](https://nba-live-win-probability-production.up.railway.app)

The deployed application is a verified live-format replay product. Actual
continuous NBA CDN ingestion remains explicitly outside the verified claim.

## Docker

- Base: `python:3.12.10-slim-bookworm`.
- Server: Gunicorn 26.0.0, one `gthread` worker, eight threads, and
  `simple-websocket`.
- PyTorch: exact 2.13.0 CPU wheel from the official CPU index; no CUDA runtime.
- Runtime user: non-root `app` (UID/GID 10001).
- Frozen model: verified during build and inside container acceptance.
- Included: application, frontend, three inference artifacts, and two authentic
  replay fixtures.
- Excluded: repository metadata, environments, secrets, caches, documentation,
  historical/training datasets, and training-only artifacts.

Verified Docker results:

| Check | Result |
|---|---:|
| Image size | 351,080,307 bytes (about 351 MB / 335 MiB) |
| Health-ready startup | 1.240–2.060 seconds |
| Idle memory | about 270–465 MiB |
| Post-replay memory | about 358–554 MiB |
| Distinct replay sequences | 434 |
| Final score | GSW 99 – BKN 125 |
| FINAL display | GSW 0% – BKN 100% |
| Raw final home probability | 0.9992812928 |
| Production lifecycle logs | verified |

The acceptance client used WebSocket-only Socket.IO against the real Gunicorn
container. It also verified `/`, `/api/health`, `/api/games`, authentic game ID,
internal model hash, non-root execution, lifecycle logs, and excluded runtime
paths.

## GitHub Actions and model integrity

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`. The
quality job installs pinned CPU dependencies, verifies the frozen hash, runs all
166 tests, compiles sources, checks dependencies, and checks whitespace. Its
dependent job builds the image and performs the full containerized WebSocket
replay acceptance.

Verified checks:

- 166 tests passed;
- compilation passed;
- `pip check` passed;
- `git diff --check` passed;
- workflow YAML and `railway.json` parsed;
- Docker build and production replay passed; and
- model SHA-256 passed inside and outside container validation.

The three production artifacts are explicitly allowlisted for source control;
evaluation and training artifacts remain ignored.

## Railway deployment

Railway builds the repository Dockerfile from `main`. `railway.json` selects
`/api/health`, a 120-second health timeout, graceful draining, and on-failure
restarts. Gunicorn binds to Railway's injected `0.0.0.0:$PORT`, and the public
domain terminates HTTPS and supports the Socket.IO WebSocket connection.

The first deployment revealed one genuine configuration issue:
`deploy.drainingSeconds` was serialized as a string. It was corrected to a JSON
number, the matching test was updated, CI passed, and the subsequent Railway
deployment reached `ACTIVE`.

Public cloud acceptance verified:

- `/api/health` returned HTTP 200 with `status=ready`, `mode=replay`,
  `model_loaded=true`, and `service_ready=true`;
- the root dashboard, stylesheet, JavaScript, and vendored Socket.IO client
  returned HTTP 200;
- an external WebSocket-only client connected through the Railway domain;
- the replay delivered 434 distinct state sequences;
- the final state was GSW 99 – BKN 125;
- the product-layer official result was GSW 0% – BKN 100%;
- the public browser rendered the final dashboard correctly; and
- deploy logs showed normal request, subscriber, poller-start, and poller-stop
  behavior with no application/runtime errors.

The only observed HTTP 404 was the browser's optional `/favicon.ico` request;
it does not affect the application.

Railway metrics during the public replay showed approximately 260 MB idle
memory, 335–340 MB after replay, and a 0.36 vCPU peak. CPU returned near idle
after the replay.

## Frozen model

- SHA-256:
  `143ca6cadca8a86d0f47ad30f0d2a8ecaee91316b4a0071540131bcada61e591`.
- Architecture: unchanged 10 -> 64 -> 32 -> 1.
- Feature order and unknown-possession semantics: unchanged.
- No retraining, tuning, calibration, preprocessing, weight, feature, adapter,
  possession, foul, or historical-pipeline changes occurred.

The backend state-processing latency baseline remains 19.372 ms median and
28.351 ms p95. The frozen model's median single-state inference is 0.316 ms.

## Production claim and limitation

The accurate portfolio claim is:

> A deployed NBA win-probability application with a verified live-format replay
> pipeline and an architecture designed for live NBA ingestion.

The live NBA schema adapter, fingerprint-based polling behavior, canonical state
generation, and frozen inference interface are covered by authentic fixtures
and deterministic tests. Historical/live semantic comparison also verified the
shared state engine on the same game.

Actual continuous Railway-to-`cdn.nba.com` ScoreBoard/PlayByPlay connectivity
has **not** been verified. The CDN previously returned HTTP 403 from the
development environment, and no active game has completed the deployed live
path. Active-game ingestion and live NBA inference are therefore not claimed.
Cloud CDN validation is documented as a future enhancement rather than a
requirement of the completed replay portfolio application.

## Interview explanation

1. Docker fixes the OS, Python, CPU PyTorch, server, code, artifacts, and start
   command.
2. The runtime excludes historical/training data because serving needs only the
   frozen inference artifacts and replay fixture.
3. CI validates both Python correctness and the real production container.
4. Chronological evaluation and shared historical/live state logic reduce data
   leakage and training-serving skew.
5. One threaded Gunicorn worker matches the in-memory Socket.IO poller design.
6. Railway health checks gate traffic while its public proxy handles HTTPS and
   WebSocket upgrades.
7. The final 0%/100% display is official product state, not a modified model
   prediction.
8. Frozen SHA-256 checks protect the selected model across source control, CI,
   image build, and container acceptance.
9. Authentic replay data proves the end-to-end application without fabricating
   a live game.
10. The CDN limitation is stated directly because a tested adapter is not the
    same as verified active-game network ingestion.

## Addendum — September 9, 2026

The findings recorded above are preserved as written at the time of Phase 8.
This addendum records a later verification result and does not revise them.

**Railway-to-`cdn.nba.com` connectivity is verified.** Testing from inside the
running production container confirmed that DNS, TLS, and HTTP all succeed
against the NBA live CDN.

**The earlier HTTP 403 was not a Railway network restriction.** It was traced to
the stale default request headers in `nba_api` 1.11.4, whose `STATS_HEADERS` set
carries a 2020-era Chrome 87 user-agent and omits `Origin` and `Referer`. The
Akamai edge in front of `cdn.nba.com` rejects that header fingerprint. Measured
from inside the deployed container, interleaved against the same edge so the two
requests differed only by headers, the library default returned HTTP 403 while a
modern browser-style set returned HTTP 200.

**A minimal shared header implementation was added.** `src/live/headers.py`
holds the verified set once, and the existing `ScoreBoard` and `PlayByPlay`
clients pass it through their established `endpoint_factory` seam into the
official `nba_api` `headers=` constructor parameter. The frozen model, feature
order, possession semantics, foul semantics, adapter behavior, and replay
determinism are unchanged.

**The production ScoreBoard path now succeeds from Railway.** Calling
`src.live.scoreboard.fetch_current_scoreboard()` inside the deployed container
returned `game_date: '2026-09-09'`, `games: 0`, and `error: None`. The `error`
field previously carried a `JSONDecodeError` from parsing the 403 HTML body, so
`error: None` confirms a genuinely decoded live response rather than a masked
failure.

**Active-game validation remains future verification.** The verified request
occurred during the NBA offseason, so the scoreboard correctly reported zero
games and no live `PlayByPlay` stream existed to exercise the path. Full
active-game `PlayByPlay` -> adapter -> canonical states -> frozen model ->
Socket.IO validation is therefore still outstanding, and live NBA inference is
still not claimed. The remaining gap is game availability, not connectivity.

**Production remains in replay mode.** The public deployment continues to serve
the deterministic, authentic live-format replay so the demo is reproducible for
reviewers rather than dependent on the NBA schedule.
