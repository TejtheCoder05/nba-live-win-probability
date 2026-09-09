# Phase 7 verification report

## Outcome

Phase 7 adds the Flask API, subscription-aware polling service, Socket.IO
delivery, authentic replay mode, and responsive dashboard without changing the
historical pipeline, shared state semantics, preprocessing, feature order,
model architecture, or weights. Replay mode remains clearly identified as a
captured game, not a current live feed.

## Application and API

| Area | Files | Responsibility |
|---|---|---|
| Flask | `src/api/app.py`, `routes.py`, `socket_events.py`, `config.py` | factory, JSON routes, event handlers, environment settings |
| Service | `src/live/service.py`, `poller.py` | discovery, one poller per game, replay, public state, error retention |
| Entrypoints | `scripts/run_app.py`, `scripts/benchmark_application.py` | local server and application-path benchmark |
| Browser | `templates/index.html`, `static/css/app.css`, `static/js/app.js` | responsive dashboard and canvas history |
| Tests | `tests/test_application.py` | API, Socket.IO, replay, poller, error and final-status contracts |

`GET /api/games` exposes normalized game summaries. `GET
/api/games/<game_id>` returns the latest state and probability histories. Replay
controls are available through `POST /api/replay/<game_id>/<action>` and the
`replay_control` Socket.IO event. Invalid and unknown IDs return structured
400/404 errors. Live discovery failure is a structured non-500 response with no
raw exception text.

## Streaming

Client events are `subscribe_game`, `unsubscribe_game`, and `replay_control`.
Server events are `game_state`, `game_status`, and `game_error`. Subscribers to
one game share one `PollerRegistry` record and background task. The final viewer
leaving signals clean shutdown. Identical response fingerprints reuse cached
state; only changed reconstructed states are broadcast. Feed failures retain the
last valid state, mark it degraded, and allow later retries.

## Dashboard

The dark, responsive sports-data interface includes oriented team names and
tricodes, scores, period/OT, clock and status, large home/away probabilities, an
animated split bar, a bounded canvas history chart with 50% guide, recent
plays, possession, period fouls, source/connection status, and first-class
start/pause/step/reset/speed replay controls. It uses no frontend framework,
external runtime CDN, team logos, or browser-side model calculation.

## Model preservation

- Model: unchanged 10 → 64 → 32 → 1 PyTorch MLP.
- Artifact SHA-256:
  `143ca6cadca8a86d0f47ad30f0d2a8ecaee91316b4a0071540131bcada61e591`.
- Feature order: score differential, period, period seconds, regulation
  seconds, overtime flag, overtime number, home possession, possession known,
  home period fouls, away period fouls—exactly the frozen V1 order.
- Unknown possession: `0.5` plus known flag `0`, unchanged.
- Raw inference remains available as `model_home_win_probability`.
- Official FINAL certainty is a separate product display rule. Tests prove
  that a non-final 0:00 state does not trigger it.

## Replay result

Fixture `tests/fixtures/live/playbyplay_0022000001.json` supplies 610 authentic
live-format actions for Golden State at Brooklyn. The existing Phase 6 adapter
and shared state engine produce 434 inferred game states and the correct final
score, Golden State 99–125 Brooklyn. Example home-probability sequence observed
through the application WebSocket:

```text
Q1 12:00, 0–0     -> 0.561034
Q2 11:35, 25–40   -> 0.874719
Q2 00:12.8, 45–60 -> 0.898549
Q3 01:13, 69–95   -> 0.987381
Q4 00:00, 99–125  -> raw 0.999281; displayed 1.000000 after official FINAL
```

Replay progressively uses the same server state and Socket.IO route as live
mode. No fake basketball events or frontend probabilities are introduced.

## Performance

`scripts/benchmark_application.py --samples 100` measured 90 post-warm-up
updates across the real fixture. The timed boundary is response available →
canonical reconstruction → shared feature generation → frozen batch inference
→ JSON-ready application state; artificial replay delay and Socket.IO network
transport are excluded.

- Median: **19.372 ms**
- p95: **28.351 ms**
- Observed min/max: 9.189 / 54.996 ms

The generated machine-readable result is
`artifacts/application_phase7_latency.json` (artifacts are intentionally
gitignored).

## Verification

- `157 passed` with all 145 frozen Phase 6 tests retained.
- `compileall` passed for `src`, `scripts`, and `tests`.
- `pip check`: no broken requirements.
- Flask routes and JSON serialization passed.
- Socket.IO connect/current/future/error/unsubscribe/disconnect tests passed.
- Two viewers reused one poller; unchanged state was not rebroadcast.
- Live HTTP 403 behavior returned a friendly structured response.
- The real local server responded to HTTP and accepted a WebSocket-only
  Socket.IO client.
- The progressive full replay reached all 610 source actions, produced multiple
  probabilities across 434 distinct state sequences (436 messages including
  control changes), applied final certainty only at official FINAL, and ended
  99–125.
- The server was stopped with its poller released; the full suite was rerun.

Browser automation was unavailable during Phase 7. Page assets, responsive CSS,
DOM update paths, and the canvas renderer were checked programmatically. Phase
8 later added manual verification of the publicly deployed Railway dashboard.

## Limitations

- `cdn.nba.com` returned HTTP 403 in this development environment. Continuous
  polling of an actually active NBA game is **not verified**, so the project is
  not claimed as production live end to end.
- The committed replay is a completed regulation game; live overtime
  normalization and timing remain covered by Phase 6 tests, not this replay.
- Possession remains deliberately unknown when evidence is insufficient; the
  model receives the frozen neutral encoding rather than a guess.
- The historical model does not itself snap at 0:00. Official FINAL is handled
  only in the application display layer.
- The public Railway replay dashboard was manually validated in Safari during
  Phase 8; browser automation is not part of the acceptance suite.

## Interview explanation

1. The browser receives predictions from the server because endpoint access,
   source adaptation, state reconstruction, and the PyTorch model belong in one
   controlled backend path; exposing NBA polling in every browser would
   duplicate traffic and bypass correctness logic.
2. Full-response fingerprints suppress unchanged work, while `PollerRegistry`
   gives all viewers of a game one shared loop.
3. Historical/live parity is preserved by adapting sources before both enter
   the same state engine and frozen preprocessor—there is no Flask feature
   pipeline.
4. Replay proves the whole application without invented data: 610 authentic
   source actions traverse adapter → state engine → model → Socket.IO → UI.
5. The model stays frozen because terminal certainty is official product state,
   not a feature. Keeping the FINAL rule outside inference avoids contaminating
   training with future information.
6. Each subscription joins a game room and increments the existing registry
   record. Only the first viewer starts work; only the last leaving stops it.
7. A new play changes the feed fingerprint, triggers deterministic full replay,
   emits a canonical game state, becomes the exact ten saved features, passes
   saved preprocessing and PyTorch, then becomes JSON sent as `game_state` and
   rendered by the dashboard.
8. Before claiming production real-time operation, the live ScoreBoard and
   PlayByPlay loops must run against an active game in an environment where the
   NBA CDN is reachable, including transient-failure observation over time.

Phase 7 originally stopped at the verified local application. Phase 8 later
added Docker, GitHub Actions, and a public Railway deployment of the same replay
path. Databases and frontend frameworks remain unnecessary for this design.
