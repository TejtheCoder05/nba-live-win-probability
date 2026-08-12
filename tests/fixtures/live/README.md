# Real NBA live-format fixtures

These files are captured responses, not invented schemas and not training data.
They are committed only for deterministic offline Phase 6 tests.

- `playbyplay_0022000001.json`: real NBA live PlayByPlay response, 610 actions,
  imported from `dblackrun/pbpstats` commit
  `e7ccf2fb6326da630a3cf1665aac964a1e108f4c`, path
  `tests/data/pbp/live_0022000001.json`.
- `game_details_0022000001.json`: matching real live game-details response from
  the same pinned commit, path
  `tests/data/game_details/live_0022000001.json`.
- `scoreboard_completed.json`: captured NBA live scoreboard response imported
  from `dblackrun/nba-stats-tracking` commit
  `682998031a8de39a96209db7a038e0d8dc318f04`, path
  `tests/data/scoreboard/response.json`.

The official live CDN returned HTTP 403 to the Phase 6 development environment,
so pinned public captures were used rather than fabricating endpoint output.
