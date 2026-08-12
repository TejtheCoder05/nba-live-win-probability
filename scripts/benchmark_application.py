#!/usr/bin/env python3
"""Benchmark fixture update through state reconstruction and application serialization."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api import create_app  # noqa: E402
from src.live.service import REPLAY_GAME_ID  # noqa: E402
from src.paths import ARTIFACTS_DIR, ensure_dir  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--output", type=Path, default=ARTIFACTS_DIR / "application_phase7_latency.json")
    args = parser.parse_args()
    if args.samples < 20:
        raise SystemExit("Use at least 20 samples")

    app = create_app({"testing": True, "start_background_tasks": False})
    service = app.extensions["game_service"]
    service.control_replay(REPLAY_GAME_ID, "reset")
    timings_ms: list[float] = []
    try:
        total_actions = service.get_state(REPLAY_GAME_ID)["replay"]["total_actions"]
        step_size = max(1, (total_actions - 1) // args.samples)
        for sample in range(args.samples):
            start = time.perf_counter_ns()
            state = service.advance_replay(step_size, emit=False)
            elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
            if sample >= 10:
                timings_ms.append(elapsed_ms)
            if state["replay"]["complete"]:
                service.control_replay(REPLAY_GAME_ID, "reset")

        result = {
            "benchmark": "fixture response -> canonical replay -> state features -> frozen model -> API-ready payload",
            "mode": "replay",
            "game_id": REPLAY_GAME_ID,
            "samples_recorded": len(timings_ms),
            "warmup_samples": 10,
            "step_size_actions": step_size,
            "median_ms": statistics.median(timings_ms),
            "p95_ms": float(np.percentile(timings_ms, 95)),
            "min_ms": min(timings_ms),
            "max_ms": max(timings_ms),
            "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        ensure_dir(args.output.parent)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        service.shutdown()


if __name__ == "__main__":
    main()
