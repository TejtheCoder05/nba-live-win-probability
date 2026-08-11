# The Bulk Downloader (Phase 2)

How several thousand historical games get onto disk without babysitting, and how
the process survives being interrupted.

## The problem

Downloading one game is easy. Downloading 1,230 games — and later several
seasons — introduces problems a single request never has:

- The run takes ~25 minutes per season, so **it will get interrupted**.
- `stats.nba.com` is unofficial. It times out, throttles, and occasionally
  returns nonsense with a 200 status code.
- A response that arrives **partially** is far more dangerous than one that
  fails outright, because a failure is loud and a truncation is silent.
- At this scale, "just re-run it" must be cheap, not another 25 minutes.

Everything below follows from those four facts.

## Components

| Module | Responsibility |
|---|---|
| `src/data/nba_client.py` | Retries, exponential backoff, jitter, request spacing (Phase 1, reused unchanged) |
| `src/data/game_log.py` | Which games exist in a season (Phase 1, reused unchanged) |
| `src/data/play_by_play.py` | Fetch one game; atomic, compressed persistence |
| `src/data/validation.py` | Is this response actually complete and correct? |
| `src/data/manifest.py` | Machine-readable per-season status; failure log |
| `src/data/downloader.py` | Orchestration: skip / fetch / validate / record |
| `scripts/download_seasons.py` | CLI, progress reporting, summary |

The split matters: `downloader.py` contains no HTTP logic and no parsing logic.
It only decides *what to do next* and *what to record*.

## The per-game decision

```
for each GAME_ID in the season:

    is there a valid file on disk?  ──yes──>  skip      (counts as "skipped")
              │
              no
              ↓
    fetch via PlayByPlayV3 (with retries/backoff)
              ↓
    validate the response
              │
        ┌─────┴─────┐
      valid      invalid
        │            │
   write to disk   raise
        │            │
   mark downloaded  mark failed + append to failure log
        └─────┬──────┘
              ↓
        save manifest
```

Two properties of this loop are load-bearing:

**The manifest is saved after every single game.** If the process is killed, at
most one game's bookkeeping is lost.

**A failure never stops the run.** One unavailable game must not cost the other
1,229. Failures are recorded and the loop continues.

## Why "the file exists" is not good enough

The resume logic interprets *a file on disk* as *this game is done*. That
inference is only safe if the file is known to be complete, so the downloader
**validates rather than checks existence**:

- Does the response's `gameId` match the game we asked for?
- Is the event stream non-empty, and long enough to be a real game (≥50 events)?
- Do `period`, `clock`, and `actionType` exist on the events?
- Do the clock values actually parse as `PT#M#S`?
- Do a majority of events carry an `actionType`?

Files are also written **atomically** — to a temp file, then renamed. A rename
is atomic at the filesystem level, so a process killed mid-write leaves either
the old file or the new one, never a half-written one.

Together these close the silent-corruption hole: `data/raw/` can be trusted
without re-verifying it against the API.

## Storage

Raw responses are stored **gzipped** (`{GAME_ID}.json.gz`). Play-by-play JSON is
highly repetitive and compresses about 23×:

| | 1 season | 3 seasons |
|---|---|---|
| pretty-printed JSON | ~400 MB | ~1.2 GB |
| gzipped | **~20 MB** | **~60 MB** |

(Measured: the 2023-24 season is 1,230 games and 20.4 MB on disk.)

The content stored is still the untouched API response — only the bytes on disk
are compressed. Reads are format-agnostic and check for `.json.gz` *and* plain
`.json`, so games downloaded during Phase 1 (before compression existed) are
still recognised as complete and are never re-downloaded.

The flat per-game CSV is **off** by default at bulk scale (`--save-csv` to
enable); it is regenerable from the JSON at any time.

## The manifest

`data/raw/manifest/{season}_{type}.json` — rewritten atomically as the run
progresses:

```json
{
  "season": "2023-24",
  "summary": {"total": 1230, "downloaded": 1230, "failed": 0, "events": 559000},
  "games": {
    "0022300001": {"status": "downloaded", "last_action": "skipped",
                   "event_count": 504, "updated_at": "..."},
    "0022300742": {"status": "failed", "error_type": "RuntimeError",
                   "error": "...", "attempts": 2, "updated_at": "..."}
  }
}
```

| Status | Meaning |
|---|---|
| `pending` | known to exist in the season, not yet downloaded |
| `downloaded` | raw file on disk and validated |
| `failed` | attempted and failed; the entry records why |

`skipped/cached` is not a stored status but a per-run *action*, recorded in
`last_action`, because whether a game was fetched *this run* is a property of
the run, not of the game.

`data/raw/manifest/failures.jsonl` is an append-only log across all runs and
seasons, recording season, GAME_ID, error type, message, attempt count, and
timestamp. The manifest holds the latest state; this holds the full history,
including failures that were later resolved.

## Rate limiting

Requests are **sequential**, spaced 1.0s apart by default (`--spacing`), on top
of the Phase 1 retry wrapper's backoff and jitter. There is deliberately **no
concurrency**. A season takes ~25 minutes at this rate; getting throttled or
IP-blocked partway through costs far more than concurrency would save.

## Recovering

```bash
# Resume an interrupted run - just run the same command again
python scripts/download_seasons.py --seasons 2023-24

# See what the manifest knows, without downloading
python scripts/download_seasons.py --seasons 2023-24 --status

# Retry only the games that previously failed
python scripts/download_seasons.py --seasons 2023-24 --only-failed
```

## Testing without downloading anything

`tests/test_downloader.py` covers the bookkeeping — the part that is painful to
debug halfway through a 1,200-game run — with **no network calls**:

- `download_season` accepts an injectable `downloader` callable, so failures can
  be induced deterministically.
- `isolated_data_dirs` redirects all output paths to `tmp_path`.

Covered: GAME_ID deduplication, skipping completed games, resume after
interruption, idempotency, corrupt/truncated/wrong-game files being rejected,
failure persistence and retry, manifest states, and compression round-tripping.

These tests earn their keep. Writing the compression tests surfaced a real bug:
a truncated `.gz` raises `zlib.error`, which is **not** an `OSError` subclass and
so escaped the exception handler — one corrupt file would have aborted an entire
1,230-game run.

## Measured results (2023-24 regular season)

| | |
|---|---|
| Games | 1,230 (from 2,460 team-level game-log rows) |
| Downloaded / skipped / failed | 1,216 / 14 / **0** |
| Play-by-play events | 598,705 (mean 486.8 per game) |
| Disk | 20.4 MB |
| Wall time | 1,223 s (~20 min) at ~1.01 games/s |
| Re-run (full resume) | 1,230 skipped, 0 downloaded, 5.1 s |

The 14 skipped games were already on disk from the Phase 1 and step A/B runs —
including 11 stored as uncompressed `.json`, which the format-agnostic reader
correctly recognised rather than re-downloading.
