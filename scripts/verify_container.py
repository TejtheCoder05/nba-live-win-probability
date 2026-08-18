#!/usr/bin/env python3
"""Verify HTTP, WebSocket replay, runtime contents, resources, and model hash."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_model_artifact import EXPECTED_SHA256  # noqa: E402


def docker(*args: str, check: bool = True) -> str:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
    ).stdout.strip()


def docker_succeeds(*args: str) -> bool:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True
    ).returncode == 0


def docker_logs(container_name: str) -> str:
    result = subprocess.run(
        ["docker", "logs", container_name],
        check=True,
        capture_output=True,
        text=True,
    )
    return f"{result.stdout}\n{result.stderr}"


def get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def wait_for_health(base_url: str, timeout: float) -> tuple[dict[str, Any], float]:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            health = get_json(f"{base_url}/api/health")
            if health.get("status") == "ready":
                return health, time.monotonic() - start
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Container did not become healthy within {timeout} seconds")


def run_socket_replay(base_url: str) -> dict[str, Any]:
    script = r"""
const io = require('./static/vendor/socket.io.min.js');
const socket = io(process.argv[1], { transports: ['websocket'], timeout: 5000 });
const states = [];
const timer = setTimeout(() => { console.error('WebSocket replay timed out'); process.exit(1); }, 60000);
socket.on('connect', () => {
  socket.emit('subscribe_game', { game_id: '0022000001' });
  socket.emit('replay_control', { game_id: '0022000001', action: 'reset' });
  socket.emit('replay_control', { game_id: '0022000001', action: 'speed', speed: 20 });
  socket.emit('replay_control', { game_id: '0022000001', action: 'start' });
});
socket.on('game_state', (state) => {
  states.push(state);
  if (!state.replay?.complete) return;
  clearTimeout(timer);
  console.log(JSON.stringify({
    updates: states.length,
    distinct_sequences: new Set(states.map(item => item.sequence)).size,
    away_score: state.away_team.score,
    home_score: state.home_team.score,
    away_probability: state.away_win_probability,
    home_probability: state.home_win_probability,
    raw_model_probability: state.model_home_win_probability,
    final_override: state.final_override_applied,
  }));
  socket.close();
  process.exit(0);
});
socket.on('connect_error', error => { clearTimeout(timer); console.error(error.message); process.exit(1); });
"""
    result = subprocess.run(
        ["node", "-e", script, base_url],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=70,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "no client output"
        raise RuntimeError(f"WebSocket replay client failed: {details}")
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="nba-win-probability:local")
    parser.add_argument("--name", default="nba-win-probability-verify")
    parser.add_argument("--port", type=int, default=5059)
    parser.add_argument("--startup-timeout", type=float, default=60)
    args = parser.parse_args()
    docker("rm", "-f", args.name, check=False)
    container_id = docker(
        "run",
        "--detach",
        "--name",
        args.name,
        "--publish",
        f"127.0.0.1:{args.port}:5000",
        "--env",
        "NBA_APP_MODE=replay",
        "--env",
        "NBA_REPLAY_SPEED=20",
        args.image,
    )
    try:
        base_url = f"http://127.0.0.1:{args.port}"
        health, startup_seconds = wait_for_health(base_url, args.startup_timeout)
        games = get_json(f"{base_url}/api/games")
        if games["games"][0]["game_id"] != "0022000001":
            raise RuntimeError("Authentic replay game is unavailable")
        root_status = urllib.request.urlopen(base_url, timeout=5).status
        if root_status != 200:
            raise RuntimeError("Dashboard root did not return 200")

        user = docker("exec", args.name, "id", "-un")
        model_hash = docker(
            "exec",
            args.name,
            "python",
            "-c",
            "import hashlib; print(hashlib.sha256(open('/app/artifacts/win_probability_v1/best_model.pt','rb').read()).hexdigest())",
        )
        if user != "app" or model_hash != EXPECTED_SHA256:
            raise RuntimeError("Container user or model integrity check failed")
        for prohibited in ("/app/data/raw", "/app/data/processed", "/app/.git", "/app/.venv"):
            if docker_succeeds("exec", args.name, "test", "-e", prohibited):
                raise RuntimeError(f"Prohibited runtime path exists: {prohibited}")

        idle_memory = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", args.name)
        replay = run_socket_replay(base_url)
        replay_memory = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", args.name)
        if (replay["away_score"], replay["home_score"]) != (99, 125):
            raise RuntimeError(f"Wrong replay final score: {replay}")
        if replay["away_probability"] != 0.0 or replay["home_probability"] != 1.0:
            raise RuntimeError(f"Official FINAL override failed: {replay}")
        logs = docker_logs(args.name)
        expected_log_messages = (
            "Frozen predictor loaded with 10 features",
            "Application initialized mode=replay",
            "Started game poller game_id=0022000001",
            "Replay control game_id=0022000001 action=start",
            "Official FINAL product override applied game_id=0022000001",
        )
        missing_logs = [message for message in expected_log_messages if message not in logs]
        if missing_logs:
            raise RuntimeError(f"Production logs are missing: {missing_logs}")
        image_size = int(docker("image", "inspect", args.image, "--format", "{{.Size}}"))
        print(
            json.dumps(
                {
                    "container_id": container_id,
                    "health": health,
                    "startup_seconds": startup_seconds,
                    "image_size_bytes": image_size,
                    "idle_memory": idle_memory,
                    "replay_memory": replay_memory,
                    "runtime_user": user,
                    "model_sha256": model_hash,
                    "production_logs_verified": True,
                    "replay": replay,
                },
                indent=2,
            )
        )
    finally:
        docker("rm", "-f", args.name, check=False)


if __name__ == "__main__":
    main()
