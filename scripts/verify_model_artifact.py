#!/usr/bin/env python3
"""Fail clearly when the frozen production model artifact changes."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_SHA256 = "143ca6cadca8a86d0f47ad30f0d2a8ecaee91316b4a0071540131bcada61e591"
MODEL_PATH = PROJECT_ROOT / "artifacts" / "win_probability_v1" / "best_model.pt"


def main() -> None:
    actual = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
    if actual != EXPECTED_SHA256:
        print(
            f"Frozen model SHA-256 mismatch: expected {EXPECTED_SHA256}, received {actual}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"Frozen model SHA-256 verified: {actual}")


if __name__ == "__main__":
    main()
