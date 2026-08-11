"""Small JSON-serializable train-only preprocessing pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from src.models.dataset import TRAIN_SEASON, V1_FEATURES

BINARY_FEATURES = frozenset({"is_overtime", "home_possession", "possession_known"})


@dataclass
class FeaturePreprocessor:
    feature_names: tuple[str, ...] = V1_FEATURES
    fitted_season: str | None = None
    means: dict[str, float] = field(default_factory=dict)
    scales: dict[str, float] = field(default_factory=dict)
    imputations: dict[str, float] = field(default_factory=lambda: {"home_possession": 0.5})

    def fit(self, frame: pd.DataFrame, *, season: str) -> "FeaturePreprocessor":
        if season != TRAIN_SEASON:
            raise ValueError(f"Preprocessing may only be fit on training season {TRAIN_SEASON}")
        self._require_features(frame.columns)
        self.means.clear()
        self.scales.clear()
        for name in self.feature_names:
            if name in BINARY_FEATURES:
                continue
            values = pd.to_numeric(frame[name], errors="raise").to_numpy(dtype=np.float64)
            mean = float(np.mean(values))
            scale = float(np.std(values))
            self.means[name] = mean
            self.scales[name] = scale if scale > 0.0 else 1.0
        self.fitted_season = season
        return self

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        self._check_fitted()
        self._require_features(frame.columns)
        columns: list[np.ndarray] = []
        for name in self.feature_names:
            values = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=np.float32, copy=True)
            if name in self.imputations:
                values[np.isnan(values)] = np.float32(self.imputations[name])
            if name not in BINARY_FEATURES:
                values = (values - np.float32(self.means[name])) / np.float32(self.scales[name])
            columns.append(values)
        result = np.column_stack(columns).astype(np.float32, copy=False)
        if not np.isfinite(result).all():
            raise ValueError("Preprocessing produced NaN or infinite model inputs")
        return result

    def transform_row(self, state: Mapping[str, object]) -> np.ndarray:
        missing = [name for name in self.feature_names if name not in state]
        if missing:
            raise KeyError(f"Missing inference features: {missing}")
        return self.transform(pd.DataFrame([{name: state[name] for name in self.feature_names}]))

    def to_dict(self) -> dict[str, object]:
        self._check_fitted()
        return {
            "version": 1,
            "feature_names": list(self.feature_names),
            "fitted_season": self.fitted_season,
            "means": self.means,
            "scales": self.scales,
            "imputations": self.imputations,
            "binary_features": sorted(BINARY_FEATURES & set(self.feature_names)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "FeaturePreprocessor":
        return cls(
            feature_names=tuple(str(item) for item in payload["feature_names"]),
            fitted_season=str(payload["fitted_season"]),
            means={str(k): float(v) for k, v in dict(payload["means"]).items()},
            scales={str(k): float(v) for k, v in dict(payload["scales"]).items()},
            imputations={str(k): float(v) for k, v in dict(payload["imputations"]).items()},
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "FeaturePreprocessor":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _check_fitted(self) -> None:
        if self.fitted_season is None:
            raise RuntimeError("FeaturePreprocessor must be fit before use")

    def _require_features(self, columns: Iterable[str]) -> None:
        available = set(columns)
        missing = [name for name in self.feature_names if name not in available]
        if missing:
            raise KeyError(f"Missing preprocessing features: {missing}")
