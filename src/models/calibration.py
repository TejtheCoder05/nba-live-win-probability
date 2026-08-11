"""JSON-safe Platt calibration fitted only from validation logits."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
from sklearn.linear_model import LogisticRegression


@dataclass(frozen=True)
class PlattCalibrator:
    coefficient: float
    intercept: float
    fitted_split: str = "validation"

    @classmethod
    def fit(cls, logits: Iterable[float], target: Iterable[float]) -> "PlattCalibrator":
        values = np.asarray(logits, dtype=np.float64).reshape(-1, 1)
        labels = np.asarray(target, dtype=np.float64)
        model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=500)
        model.fit(values, labels)
        return cls(coefficient=float(model.coef_[0, 0]), intercept=float(model.intercept_[0]))

    def transform_logits(self, logits: Iterable[float]) -> np.ndarray:
        values = np.asarray(logits, dtype=np.float64)
        calibrated_logits = self.coefficient * values + self.intercept
        return (1.0 / (1.0 + np.exp(-np.clip(calibrated_logits, -50.0, 50.0)))).astype(np.float64)

    def to_dict(self) -> dict[str, object]:
        return {
            "method": "platt",
            "coefficient": self.coefficient,
            "intercept": self.intercept,
            "fitted_split": self.fitted_split,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PlattCalibrator":
        if payload.get("method") != "platt":
            raise ValueError("Unsupported calibration method")
        return cls(
            coefficient=float(payload["coefficient"]),
            intercept=float(payload["intercept"]),
            fitted_split=str(payload["fitted_split"]),
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "PlattCalibrator":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
