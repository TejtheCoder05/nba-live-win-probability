# Phase 6 verification report

## Outcome

Live NBA ingestion, source adaptation, deterministic offline replay,
historical/live feature parity, and frozen-model inference are implemented.
The Phase 5 model, preprocessing, feature list, data splits, and artifacts were
not retrained, retuned, recalibrated, or redesigned.

## Live API inspection

- Installed version: `nba_api==1.11.4`.
- Interfaces: `nba_api.live.nba.endpoints.ScoreBoard` and `PlayByPlay`.
- Official host: `cdn.nba.com/static/json/liveData/`.
- Direct current scoreboard and completed-game PBP calls returned HTTP 403
  Access Denied from this environment. The new scoreboard interface returned an
  empty error-bearing snapshot rather than crashing; PBP raises a descriptive
  `LiveEndpointError`.
- Current game availability is therefore unknown, not fabricated as zero.
- Real pinned public response captures provide offline scoreboard, completed
  live PBP, and matching live game-details fixtures.
- The historical PlayByPlayV3 endpoint was reachable for that same game and its
  response was captured separately for offline parity.

The exact observed schemas and historical/live field differences are documented
in [LIVE_DATA.md](LIVE_DATA.md).

## Implementation

| File | Responsibility |
|---|---|
| `src/live/scoreboard.py` | scoreboard retrieval, no-game/error handling, oriented normalization |
| `src/live/play_by_play.py` | one-shot PBP fetch, validation, spacing, fingerprints, atomic raw cache |
| `src/live/adapter.py` | live schema to canonical shared action contract |
| `src/live/replay.py` | shared Phase 3 replay, exact feature extraction, frozen prediction |
| `src/live/parity.py` | action-anchor and semantic-state parity metrics |
| `src/live/errors.py` | endpoint/schema boundary errors |
| `scripts/replay_live_game.py` | deterministic offline state/probability replay |
| `scripts/verify_live_parity.py` | same-game historical/live comparison |

The only historical-engine refactor was allowing expected final scores and
`home_win` to be absent for an in-progress live game. Historical callers and
their emitted schema remain unchanged. Score, clock, possession, foul, and
state-emission logic are the same Phase 3 implementation.

## Same-game parity: `0022000001`

The real game was Golden State at Brooklyn, final 99–125 from the away/home
orientation. Historical V3 contained 547 actions and emitted 438 states. The
live-format response contained 610 richer actions and emitted 434 states after
canonical adaptation. Raw row counts are intentionally not required to match.

There were 428 emitted states aligned by common action number:

| Field | Agreement |
|---|---:|
| Home/away score | 100.000% |
| Score differential | 100.000% |
| Period | 100.000% |
| Clock | 100.000% |
| Period seconds remaining | 100.000% |
| Regulation seconds remaining | 100.000% |
| Overtime flag/number | 100.000% |
| Home period fouls | 100.000% |
| Away period fouls | 100.000% |
| Possession team | 98.131% |
| Possession known/unknown | 98.131% |

Both paths reconstructed the official 125–99 final score. Of 405 unique
`(period, clock, home score, away score)` semantic anchors in each path, 404
matched (99.753%).

The eight possession disagreements at aligned action states are explainable:
the live capture supplies team ownership on some team-rebound rows where the
historical V3 response has no usable `teamId`. Live can therefore resolve those
states through the same pending-miss rule while historical correctly remains
unknown. There is no guessed ownership and no hidden use of future events.

## Frozen-model smoke test

The exact saved feature order was loaded from `preprocessing.json` and compared
with the live extractor. A real Q2 7:48 state produced:

```text
[18, 2, 468.0, 1908.0, 0, 0, 1.0, 1, 1, 3]
```

After unchanged frozen preprocessing and inference, the probability was
`0.916364`. Full replay produced 434 states and 434 valid probabilities in
`[0, 1]`. The final raw probability was `0.999281` for the 26-point home win;
it was not manually overridden.

Frozen `best_model.pt` SHA-256 before Phase 6 was:

```text
143ca6cadca8a86d0f47ad30f0d2a8ecaee91316b4a0071540131bcada61e591
```

Final verification confirms the same hash.

## Replay and correction behavior

- Real live actions replayed: 610.
- Shared emitted states: 434.
- Known possession: 95.853% of live-derived states.
- Final score: 125–99.
- Identical replay responses have the same fingerprint and return the cached
  state with `changed=False`.
- Changed or corrected feeds trigger a deterministic full rebuild.
- `orderNumber` is used because the real response had 11 backward
  `actionNumber` transitions but zero backward `orderNumber` transitions.
- Duplicate source identities do not double-count.

## Error and no-game behavior

Deterministic tests cover empty scoreboards, scheduled/active/halftime/final
normalization, missing optional values, malformed PBP, wrong game IDs, endpoint
timeouts/decoding failures, empty action lists, unexpected action types,
duplicate events, and identical repeated responses. Ordinary tests never call
NBA endpoints.

## Final verification

- 145/145 tests pass, including all 115 frozen Phase 5 tests.
- `compileall` succeeds for `src/`, `scripts/`, and `tests/`.
- `pip check` reports no broken requirements.
- Offline replay and same-game parity CLIs both complete successfully.
- Frozen `best_model.pt` SHA-256 is unchanged.

## Limitations

- The official live CDN was HTTP 403 from the development environment, so an
  actually active game could not be observed directly during implementation.
  Production network access still needs an environment-level smoke check.
- The real replay fixture is a completed regulation game. Overtime scoreboard
  normalization and the shared overtime clock formula are tested, but the
  replay fixture itself has no overtime actions.
- Corrected feeds are detected by whole-response fingerprint and safely rebuilt;
  the code does not attempt a more complex incremental event rollback.
- Unexpected event types are logged/preserved as metadata and do not invent
  possession changes.
- The frozen model still does not snap close final games to 0%/100%. A future
  product layer may override display only after official final status.

## Interview explanation

1. Historical and live endpoints encode the same basketball actions with
   different labels and fields, so source adapters isolate that schema churn.
2. Both sources must converge before state reconstruction; otherwise two rule
   engines would drift.
3. Reusing the Phase 3 state engine preserves score, clock, possession, foul,
   and emission semantics learned by the model.
4. Exact feature meaning/order and frozen preprocessing prevent
   training-serving skew.
5. Possession remains unknown when equivalent evidence is insufficient; 0.5
   plus `possession_known=0` reaches the model exactly as during training.
6. Full-response fingerprints make repeated polls idempotent; changed feeds are
   rebuilt, so corrections cannot be double-counted.
7. Live states pass through the saved preprocessor and PyTorch weights via the
   existing `WinProbabilityPredictor` interface.
8. Final certainty is official product state, not a historical model feature;
   applying it only after status FINAL avoids future-information leakage.

Phase 6 stops here. Flask, Socket.IO, WebSockets, frontend/dashboard, and
deployment remain unimplemented pending approval.
