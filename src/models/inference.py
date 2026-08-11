"""Disk-backed one-state inference interface for future live integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import numpy as np
import torch

from src.models.calibration import PlattCalibrator
from src.models.model import ModelConfig, WinProbabilityMLP
from src.models.preprocessing import FeaturePreprocessor


class WinProbabilityPredictor:
    def __init__(
        self,
        model: WinProbabilityMLP,
        preprocessor: FeaturePreprocessor,
        calibrator: PlattCalibrator | None = None,
    ) -> None:
        self.model = model.to("cpu").eval()
        self.preprocessor = preprocessor
        self.calibrator = calibrator

    @classmethod
    def load(cls, artifact_directory: Path | str) -> "WinProbabilityPredictor":
        directory = Path(artifact_directory)
        config = ModelConfig.from_dict(json.loads((directory / "model_config.json").read_text(encoding="utf-8")))
        model = WinProbabilityMLP(config)
        state = torch.load(directory / "best_model.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        preprocessor = FeaturePreprocessor.load(directory / "preprocessing.json")
        calibration_path = directory / "calibration.json"
        calibrator = PlattCalibrator.load(calibration_path) if calibration_path.exists() else None
        return cls(model, preprocessor, calibrator)

    def predict_one(self, state: Mapping[str, object]) -> float:
        features = self.preprocessor.transform_row(state)
        with torch.inference_mode():
            logit = float(self.model(torch.from_numpy(features))[0])
        if self.calibrator is not None:
            probability = float(self.calibrator.transform_logits([logit])[0])
        else:
            probability = float(torch.sigmoid(torch.tensor(logit)))
        if not 0.0 <= probability <= 1.0:
            raise RuntimeError("Model produced an invalid probability")
        return probability

    def predict_batch(self, states: list[Mapping[str, object]]) -> np.ndarray:
        if not states:
            return np.empty(0, dtype=np.float64)
        missing = [name for name in self.preprocessor.feature_names if any(name not in state for state in states)]
        if missing:
            raise KeyError(f"Missing inference features: {missing}")
        import pandas as pd

        features = self.preprocessor.transform(
            pd.DataFrame([{name: state[name] for name in self.preprocessor.feature_names} for state in states])
        )
        with torch.inference_mode():
            logits = self.model(torch.from_numpy(features)).numpy().astype(np.float64)
        if self.calibrator is not None:
            return self.calibrator.transform_logits(logits)
        return 1.0 / (1.0 + np.exp(-np.clip(logits, -50.0, 50.0)))
