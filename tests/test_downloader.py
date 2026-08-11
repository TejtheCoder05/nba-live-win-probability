"""Tests for the resumable bulk downloader.

None of these hit the network. Two isolation techniques are used:

* ``isolated_data_dirs`` redirects every module-level output path at tmp_path,
  so tests never read or write the real ``data/raw/``.
* ``download_season`` accepts an injectable ``downloader`` callable, so the
  orchestration logic (skip / resume / fail / count) can be driven with
  synthetic responses and deliberately induced failures.

What matters here is the *bookkeeping*, which is exactly the part that is
painful to debug halfway through a 1,200-game download.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.data import downloader as downloader_module
from src.data import manifest as manifest_module
from src.data import play_by_play as pbp_module
from src.data.downloader import download_season, is_game_complete
from src.data.game_log import build_game_index, unique_game_ids
from src.data.manifest import STATUS_DOWNLOADED, STATUS_FAILED, DownloadManifest, failure_log_path
from src.data.play_by_play import play_by_play_paths, save_raw_play_by_play
from src.data.validation import validate_play_by_play

SEASON = "2023-24"
SEASON_TYPE = "Regular Season"


# --- Synthetic data ---------------------------------------------------------


def make_raw_response(game_id: str, n_events: int = 120) -> dict:
    """A structurally valid PlayByPlayV3 response, shaped like the real thing."""
    actions = []
    for i in range(n_events):
        minutes, seconds = divmod(max(0, 720 - i * 5), 60)
        actions.append(
            {
                "actionNumber": i + 1,
                "clock": f"PT{minutes:02d}M{seconds:02d}.00S",
                "period": 1 + i // 40,
                "teamTricode": "LAL" if i % 2 else "DEN",
                "scoreHome": str(i) if i % 3 == 0 else "",
                "scoreAway": str(i) if i % 3 == 0 else "",
                "actionType": "Made Shot" if i % 4 else "Rebound",
                "subType": "Jump Shot",
                "description": f"event {i}",
            }
        )
    return {"meta": {}, "game": {"gameId": game_id, "videoAvailable": 0, "actions": actions}}


def make_game_log(n_games: int = 6) -> pd.DataFrame:
    """A team-level game log: two rows per game, exactly like LeagueGameLog."""
    rows = []
    for i in range(n_games):
        game_id = f"00223000{i:02d}"
        rows.append(
            {
                "GAME_ID": game_id,
                "GAME_DATE": f"2023-10-{24 + i:02d}",
                "TEAM_ID": 1610612747,
                "TEAM_ABBREVIATION": "LAL",
                "MATCHUP": "LAL @ DEN",
                "WL": "L",
                "PTS": 100 + i,
            }
        )
        rows.append(
            {
                "GAME_ID": game_id,
                "GAME_DATE": f"2023-10-{24 + i:02d}",
                "TEAM_ID": 1610612743,
                "TEAM_ABBREVIATION": "DEN",
                "MATCHUP": "DEN vs. LAL",
                "WL": "W",
                "PTS": 110 + i,
            }
        )
    return pd.DataFrame(rows)


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def isolated_data_dirs(tmp_path, monkeypatch):
    """Point all writable data paths at tmp_path.

    The modules import path constants into their own namespace, so each module's
    copy must be patched individually.
    """
    pbp_dir = tmp_path / "playbyplay"
    manifest_dir = tmp_path / "manifest"
    pbp_dir.mkdir()
    manifest_dir.mkdir()

    monkeypatch.setattr(pbp_module, "PLAY_BY_PLAY_DIR", pbp_dir)
    monkeypatch.setattr(manifest_module, "MANIFEST_DIR", manifest_dir)
    return tmp_path


@pytest.fixture
def stub_game_log(monkeypatch):
    """Make the season game log local, so no API call is needed for the index."""
    game_log = make_game_log()
    monkeypatch.setattr(
        downloader_module,
        "load_cached_game_log",
        lambda season, season_type: game_log,
    )
    return game_log


@pytest.fixture
def fake_downloader():
    """A stand-in for download_game that records calls and can be made to fail."""

    class FakeDownloader:
        def __init__(self):
            self.calls: list[str] = []
            self.fail_on: set[str] = set()
            self.events = 120

        def __call__(self, game_id: str, save_csv: bool = False, spacing: float = 0.0) -> int:
            self.calls.append(game_id)
            if game_id in self.fail_on:
                raise RuntimeError(f"simulated network failure for {game_id}")
            raw = make_raw_response(game_id, self.events)
            events = pd.DataFrame(raw["game"]["actions"])
            save_raw_play_by_play(game_id, events, raw, save_csv=save_csv)
            return self.events

    return FakeDownloader()


def run(fake_downloader, **kwargs):
    """Convenience wrapper: a download run with sensible test defaults."""
    options = {
        "season": SEASON,
        "season_type": SEASON_TYPE,
        "spacing": 0.0,
        "progress_every": 100,
        "downloader": fake_downloader,
    }
    options.update(kwargs)
    return download_season(**options)


# --- GAME_ID deduplication --------------------------------------------------


def test_dedup_game_id_count_is_correct():
    """The team-level game log must collapse to exactly half as many games."""
    game_log = make_game_log(n_games=6)

    assert len(game_log) == 12, "fixture should have two rows per game"
    assert len(unique_game_ids(game_log)) == 6
    assert len(build_game_index(game_log)) == 6


def test_dedup_survives_duplicate_rows():
    """Accidental duplicate rows must not inflate the game count."""
    game_log = pd.concat([make_game_log(4), make_game_log(4)], ignore_index=True)

    assert len(game_log) == 16
    assert len(unique_game_ids(game_log)) == 4


def test_downloader_targets_every_unique_game_once(isolated_data_dirs, stub_game_log, fake_downloader):
    stats = run(fake_downloader)

    assert stats.total == 6
    assert stats.downloaded == 6
    assert len(fake_downloader.calls) == 6
    assert len(set(fake_downloader.calls)) == 6, "no game should be downloaded twice"


# --- Skipping already-downloaded games --------------------------------------


def test_already_downloaded_games_are_skipped(isolated_data_dirs, stub_game_log, fake_downloader):
    """A second run must download nothing and skip everything."""
    first = run(fake_downloader)
    assert first.downloaded == 6 and first.skipped == 0

    fake_downloader.calls.clear()
    second = run(fake_downloader)

    assert second.downloaded == 0
    assert second.skipped == 6
    assert fake_downloader.calls == [], "no API calls should be made on a full re-run"


def test_skip_is_decided_by_the_file_not_the_manifest(isolated_data_dirs, stub_game_log, fake_downloader):
    """Deleting a raw file must cause a re-download even though the manifest
    still says 'downloaded'. The filesystem is the source of truth."""
    run(fake_downloader)

    json_path, _ = play_by_play_paths("0022300003")
    json_path.unlink()

    fake_downloader.calls.clear()
    stats = run(fake_downloader)

    assert fake_downloader.calls == ["0022300003"]
    assert stats.downloaded == 1
    assert stats.skipped == 5


# --- Invalid / incomplete cached files --------------------------------------


@pytest.mark.parametrize(
    "name,payload",
    [
        ("truncated json", '{"game": {"gameId": "0022300000", "actions": ['),
        ("empty actions", json.dumps({"game": {"gameId": "0022300000", "actions": []}})),
        ("no game object", json.dumps({"meta": {}})),
        ("html error page", "<html><body>Service Unavailable</body></html>"),
        ("empty file", ""),
    ],
)
def test_malformed_cached_files_are_not_complete(isolated_data_dirs, name, payload):
    game_id = "0022300000"
    json_path, _ = play_by_play_paths(game_id)
    json_path.write_text(payload, encoding="utf-8")

    assert not is_game_complete(game_id), f"{name} must not count as a complete download"


def test_wrong_game_id_in_response_is_rejected(isolated_data_dirs):
    """A response for a different game must never be accepted as cached."""
    json_path, _ = play_by_play_paths("0022300000")
    json_path.write_text(json.dumps(make_raw_response("0099999999")), encoding="utf-8")

    assert not is_game_complete("0022300000")

    result = validate_play_by_play(make_raw_response("0099999999"), "0022300000")
    assert not result.is_valid
    assert "mismatch" in result.reason


def test_truncated_event_stream_is_rejected(isolated_data_dirs):
    """A response cut short mid-game is structurally valid JSON but incomplete."""
    result = validate_play_by_play(make_raw_response("0022300000", n_events=5), "0022300000")

    assert not result.is_valid
    assert "truncated" in result.reason


def test_valid_response_passes_validation():
    result = validate_play_by_play(make_raw_response("0022300000"), "0022300000")

    assert result.is_valid, result.reason
    assert result.event_count == 120


def test_invalid_cached_file_is_redownloaded(isolated_data_dirs, stub_game_log, fake_downloader):
    """A corrupt file on disk must be replaced, not skipped."""
    run(fake_downloader)

    corrupt_id = "0022300002"
    json_path, _ = play_by_play_paths(corrupt_id)
    json_path.write_text('{"game": {"actions": [', encoding="utf-8")

    fake_downloader.calls.clear()
    stats = run(fake_downloader)

    assert fake_downloader.calls == [corrupt_id]
    assert stats.downloaded == 1
    assert is_game_complete(corrupt_id), "the corrupt file should now be repaired"


# --- Failure tracking -------------------------------------------------------


def test_failed_game_ids_are_persisted(isolated_data_dirs, stub_game_log, fake_downloader):
    fake_downloader.fail_on = {"0022300001", "0022300004"}
    stats = run(fake_downloader)

    assert stats.failed == 2
    assert stats.downloaded == 4
    assert sorted(stats.failed_game_ids) == ["0022300001", "0022300004"]

    manifest = DownloadManifest.load(SEASON, SEASON_TYPE)
    assert manifest.failed == ["0022300001", "0022300004"]

    entry = manifest.games["0022300001"]
    assert entry["status"] == STATUS_FAILED
    assert entry["error_type"] == "RuntimeError"
    assert "simulated network failure" in entry["error"]
    assert entry["attempts"] == 1
    assert "updated_at" in entry


def test_failures_are_appended_to_the_failure_log(isolated_data_dirs, stub_game_log, fake_downloader):
    """The append-only log keeps history the manifest overwrites."""
    fake_downloader.fail_on = {"0022300001"}
    run(fake_downloader)
    run(fake_downloader)  # fails a second time

    records = [json.loads(line) for line in failure_log_path().read_text().splitlines()]

    assert len(records) == 2, "each failure should append its own record"
    assert {record["game_id"] for record in records} == {"0022300001"}
    assert records[0]["season"] == SEASON
    assert records[0]["error_type"] == "RuntimeError"
    assert [record["attempts"] for record in records] == [1, 2]


def test_a_failure_does_not_stop_the_run(isolated_data_dirs, stub_game_log, fake_downloader):
    """One bad game must not abort the remaining 1,200."""
    fake_downloader.fail_on = {"0022300000"}
    stats = run(fake_downloader)

    assert stats.failed == 1
    assert stats.downloaded == 5, "later games must still be attempted"


def test_failed_games_are_retried_on_the_next_run(isolated_data_dirs, stub_game_log, fake_downloader):
    fake_downloader.fail_on = {"0022300001"}
    run(fake_downloader)

    fake_downloader.fail_on = set()  # the network recovers
    fake_downloader.calls.clear()
    stats = run(fake_downloader)

    assert fake_downloader.calls == ["0022300001"]
    assert stats.downloaded == 1
    assert stats.failed == 0

    manifest = DownloadManifest.load(SEASON, SEASON_TYPE)
    assert manifest.failed == []
    assert manifest.games["0022300001"]["status"] == STATUS_DOWNLOADED
    assert "error" not in manifest.games["0022300001"], "a resolved failure must clear its error"


def test_only_game_ids_restricts_the_run(isolated_data_dirs, stub_game_log, fake_downloader):
    """This is what --only-failed uses."""
    fake_downloader.fail_on = {"0022300002"}
    run(fake_downloader)

    fake_downloader.fail_on = set()
    fake_downloader.calls.clear()
    stats = run(fake_downloader, only_game_ids=["0022300002"])

    assert fake_downloader.calls == ["0022300002"]
    assert stats.considered == 1


# --- Resume behaviour -------------------------------------------------------


def test_resume_continues_where_it_left_off(isolated_data_dirs, stub_game_log, fake_downloader):
    """Simulate an interruption partway through, then restart."""
    partial = run(fake_downloader, limit=2)
    assert partial.downloaded == 2

    fake_downloader.calls.clear()
    resumed = run(fake_downloader)

    assert resumed.skipped == 2, "the first two games should be recognised as done"
    assert resumed.downloaded == 4
    assert fake_downloader.calls == [
        "0022300002",
        "0022300003",
        "0022300004",
        "0022300005",
    ], "only the not-yet-downloaded games should be fetched"


def test_resume_is_idempotent(isolated_data_dirs, stub_game_log, fake_downloader):
    """Running repeatedly must converge, not accumulate duplicates or events."""
    run(fake_downloader)
    run(fake_downloader)
    final = run(fake_downloader)

    assert final.downloaded == 0
    assert final.skipped == 6

    manifest = DownloadManifest.load(SEASON, SEASON_TYPE)
    assert len(manifest.games) == 6
    assert len(manifest.downloaded) == 6
    assert manifest.total_events() == 6 * 120


def test_manifest_survives_corruption(isolated_data_dirs, stub_game_log, fake_downloader):
    """A destroyed manifest must not cause a full re-download.

    The raw files are the source of truth; the manifest is a convenience index
    that can be rebuilt from them.
    """
    run(fake_downloader)

    manifest_module.manifest_path(SEASON, SEASON_TYPE).write_text("{not json", encoding="utf-8")

    fake_downloader.calls.clear()
    stats = run(fake_downloader)

    assert fake_downloader.calls == [], "files on disk should still be recognised"
    assert stats.skipped == 6


def test_event_counts_are_rebuilt_after_manifest_loss(isolated_data_dirs, stub_game_log, fake_downloader):
    """Skipped games must report the count read from the file, not from the
    manifest, so a lost manifest does not silently zero out event totals."""
    run(fake_downloader)
    manifest_module.manifest_path(SEASON, SEASON_TYPE).unlink()

    run(fake_downloader)

    rebuilt = DownloadManifest.load(SEASON, SEASON_TYPE)
    assert rebuilt.total_events() == 6 * 120, "counts should be recovered from the raw files"


# --- Manifest state ---------------------------------------------------------


def test_manifest_records_pending_downloaded_and_failed(isolated_data_dirs, stub_game_log, fake_downloader):
    """The manifest must answer 'what is pending / downloaded / failed?'."""
    fake_downloader.fail_on = {"0022300005"}
    run(fake_downloader, limit=6)

    manifest = DownloadManifest.load(SEASON, SEASON_TYPE)
    summary = manifest.summary()

    assert summary["total"] == 6
    assert summary["downloaded"] == 5
    assert summary["failed"] == 1
    assert summary["pending"] == 0
    assert summary["events"] == 5 * 120


def test_unattempted_games_remain_pending(isolated_data_dirs, stub_game_log, fake_downloader):
    """Games beyond --limit are registered but not yet attempted."""
    run(fake_downloader, limit=2)

    manifest = DownloadManifest.load(SEASON, SEASON_TYPE)

    assert len(manifest.downloaded) == 2
    assert len(manifest.pending) == 4
    assert manifest.games_needing_download() == manifest.pending


def test_skipped_games_are_distinguishable_from_freshly_downloaded(
    isolated_data_dirs, stub_game_log, fake_downloader
):
    run(fake_downloader, limit=1)
    run(fake_downloader, limit=2)

    manifest = DownloadManifest.load(SEASON, SEASON_TYPE)

    assert manifest.games["0022300000"]["last_action"] == "skipped"
    assert manifest.games["0022300001"]["last_action"] == "downloaded"


def test_manifest_is_written_as_valid_json(isolated_data_dirs, stub_game_log, fake_downloader):
    """It is a machine-readable artifact, so it must actually parse."""
    run(fake_downloader, limit=3)

    payload = json.loads(manifest_module.manifest_path(SEASON, SEASON_TYPE).read_text())

    assert payload["season"] == SEASON
    assert payload["season_type"] == SEASON_TYPE
    assert payload["summary"]["downloaded"] == 3
    assert len(payload["games"]) == 6
    assert set(payload["games"]["0022300000"]) >= {"status", "event_count", "updated_at"}


# --- Persistence details ----------------------------------------------------


def test_csv_is_not_written_by_default(isolated_data_dirs, stub_game_log, fake_downloader):
    """At season scale the CSV is redundant, so bulk runs skip it."""
    run(fake_downloader, limit=1)

    json_path, csv_path = play_by_play_paths("0022300000")
    assert json_path.exists()
    assert not csv_path.exists()


def test_csv_is_written_when_requested(isolated_data_dirs, stub_game_log, fake_downloader):
    run(fake_downloader, limit=1, save_csv=True)

    json_path, csv_path = play_by_play_paths("0022300000")
    assert json_path.exists()
    assert csv_path.exists()


def test_no_temp_files_are_left_behind(isolated_data_dirs, stub_game_log, fake_downloader):
    """Atomic writes must clean up after themselves."""
    run(fake_downloader)

    leftovers = list(pbp_module.PLAY_BY_PLAY_DIR.glob("*.tmp"))
    assert leftovers == []


# --- Compression ------------------------------------------------------------


def test_raw_files_are_gzipped_by_default(isolated_data_dirs, stub_game_log, fake_downloader):
    run(fake_downloader, limit=1)

    assert (pbp_module.PLAY_BY_PLAY_DIR / "0022300000.json.gz").exists()
    assert not (pbp_module.PLAY_BY_PLAY_DIR / "0022300000.json").exists()


def test_gzipped_response_round_trips_unchanged(isolated_data_dirs):
    """Compression must not alter the stored response in any way."""
    original = make_raw_response("0022300000")
    path, _ = play_by_play_paths("0022300000")

    pbp_module.write_raw_json(path, original)

    assert pbp_module.read_raw_json(path) == original


def test_plain_json_from_phase_1_is_still_recognised(isolated_data_dirs, stub_game_log, fake_downloader):
    """Games downloaded before compression existed must NOT be re-downloaded.

    This is the backward-compatibility guarantee: switching storage formats is
    not allowed to invalidate data already on disk.
    """
    game_id = "0022300000"
    plain_path = pbp_module.PLAY_BY_PLAY_DIR / f"{game_id}.json"
    plain_path.write_text(json.dumps(make_raw_response(game_id)), encoding="utf-8")

    assert is_game_complete(game_id), "an uncompressed but valid file is still complete"

    stats = run(fake_downloader, limit=1)

    assert fake_downloader.calls == []
    assert stats.skipped == 1


def test_corrupt_gzip_is_not_treated_as_complete(isolated_data_dirs):
    """A truncated .gz must fail validation rather than raise."""
    game_id = "0022300000"
    path, _ = play_by_play_paths(game_id)
    path.write_bytes(b"\x1f\x8b\x08\x00 truncated garbage")

    assert not is_game_complete(game_id)


def test_compressed_file_is_substantially_smaller(isolated_data_dirs):
    """Sanity-check the reason compression was introduced at all."""
    raw = make_raw_response("0022300000", n_events=400)

    gz_path, _ = play_by_play_paths("0022300000", compress=True)
    plain_path, _ = play_by_play_paths("0022300000", compress=False)
    pbp_module.write_raw_json(gz_path, raw)
    pbp_module.write_raw_json(plain_path, raw)

    assert gz_path.stat().st_size < plain_path.stat().st_size / 5
