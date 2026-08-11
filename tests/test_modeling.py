from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
import torch

from src.models.calibration import PlattCalibrator
from src.models.dataset import (
    TARGET_COLUMN,
    TRAIN_SEASON,
    V1_FEATURES,
    SeasonSplit,
    assert_isolated_splits,
    features_and_target,
    game_balanced_weights,
    time_bucket_sample,
)
from src.models.inference import WinProbabilityPredictor
from src.models.metrics import calibration_bins, expected_calibration_error, probability_metrics
from src.models.model import ModelConfig, WinProbabilityMLP, logits_to_probabilities
from src.models.preprocessing import FeaturePreprocessor
from src.models.training import make_loader, predict_logits


def model_frame(game_ids: tuple[str, ...] = ("1", "2"), season: str = TRAIN_SEASON) -> pd.DataFrame:
    rows = []
    for game_index, game_id in enumerate(game_ids):
        for state_index in range(4):
            rows.append(
                {
                    "season": season,
                    "game_id": game_id,
                    "score_differential": state_index - 1,
                    "period": state_index + 1,
                    "seconds_remaining_period": 720 - state_index * 120,
                    "seconds_remaining_regulation": 2880 - state_index * 720,
                    "is_overtime": False,
                    "overtime_number": 0,
                    "home_possession": np.nan if state_index == 0 else bool(state_index % 2),
                    "possession_known": state_index != 0,
                    "home_team_fouls_period": state_index,
                    "away_team_fouls_period": state_index + 1,
                    "home_win": game_index % 2,
                }
            )
    return pd.DataFrame(rows)


def fitted_preprocessor(frame: pd.DataFrame | None = None) -> FeaturePreprocessor:
    source = frame if frame is not None else model_frame()
    features, _ = features_and_target(source)
    return FeaturePreprocessor().fit(features, season=TRAIN_SEASON)


def test_features_exclude_target_and_identifiers() -> None:
    assert TARGET_COLUMN not in V1_FEATURES
    assert not {"game_id", "home_team_id", "away_team_id", "action_id"} & set(V1_FEATURES)


def test_target_leakage_is_rejected() -> None:
    with pytest.raises(ValueError, match="Target leakage"):
        features_and_target(model_frame(), features=(*V1_FEATURES, TARGET_COLUMN))


def test_split_isolation_and_chronology() -> None:
    train = SeasonSplit("train", "2021-22", model_frame(("1",)))
    validation = SeasonSplit("validation", "2022-23", model_frame(("2",), "2022-23"))
    test = SeasonSplit("test", "2023-24", model_frame(("3",), "2023-24"))
    assert_isolated_splits([train, validation, test])


def test_split_game_overlap_is_rejected() -> None:
    train = SeasonSplit("train", "2021-22", model_frame(("1",)))
    validation = SeasonSplit("validation", "2022-23", model_frame(("1",), "2022-23"))
    with pytest.raises(ValueError, match="Game overlap"):
        assert_isolated_splits([train, validation])


def test_split_rejects_mislabeled_frame_season() -> None:
    validation = SeasonSplit("validation", "2022-23", model_frame(("2",), "2021-22"))
    with pytest.raises(ValueError, match="frame must contain only"):
        assert_isolated_splits([validation])


def test_preprocessor_can_only_fit_training_season() -> None:
    features, _ = features_and_target(model_frame())
    with pytest.raises(ValueError, match="training season"):
        FeaturePreprocessor().fit(features, season="2022-23")


def test_preprocessor_preserves_order_imputes_unknown_and_is_finite() -> None:
    frame = model_frame()
    features, _ = features_and_target(frame)
    preprocessor = fitted_preprocessor(frame)
    transformed = preprocessor.transform(features)
    assert preprocessor.feature_names == V1_FEATURES
    assert transformed.shape == (len(frame), len(V1_FEATURES))
    assert np.isfinite(transformed).all()
    possession_index = V1_FEATURES.index("home_possession")
    assert transformed[0, possession_index] == pytest.approx(0.5)


def test_preprocessor_round_trip(tmp_path) -> None:
    preprocessor = fitted_preprocessor()
    path = tmp_path / "preprocessing.json"
    preprocessor.save(path)
    loaded = FeaturePreprocessor.load(path)
    assert loaded.to_dict() == preprocessor.to_dict()


def test_unknown_possession_can_be_kept_or_dropped() -> None:
    frame = model_frame()
    kept, _ = features_and_target(frame)
    dropped, _ = features_and_target(frame, drop_unknown_possession=True)
    assert len(kept) == 8
    assert len(dropped) == 6


def test_game_balanced_weights_give_equal_game_totals() -> None:
    frame = pd.concat([model_frame(("1",)).iloc[:2], model_frame(("2",)).iloc[:4]], ignore_index=True)
    weights = game_balanced_weights(frame)
    totals = pd.Series(weights).groupby(frame["game_id"]).sum()
    assert totals.iloc[0] == pytest.approx(totals.iloc[1])
    assert weights.mean() == pytest.approx(1.0)


def test_time_bucket_sampling_never_increases_rows() -> None:
    frame = model_frame()
    sampled = time_bucket_sample(frame, seconds=30)
    assert 0 < len(sampled) <= len(frame)
    assert set(sampled["game_id"]) == set(frame["game_id"])


def test_tensor_loader_dimensions_and_model_output() -> None:
    features = np.ones((7, len(V1_FEATURES)), dtype=np.float32)
    target = np.ones(7, dtype=np.float32)
    batch_features, batch_target = next(iter(make_loader(features, target, batch_size=4, shuffle=False)))
    model = WinProbabilityMLP(ModelConfig(input_size=len(V1_FEATURES)))
    assert batch_features.shape == (4, len(V1_FEATURES))
    assert batch_target.shape == (4,)
    assert model(batch_features).shape == (4,)
    assert predict_logits(model, features).shape == (7,)


def test_logits_convert_to_valid_probabilities() -> None:
    probability = logits_to_probabilities(torch.tensor([-100.0, 0.0, 100.0]))
    assert torch.all((0.0 <= probability) & (probability <= 1.0))
    assert probability[1].item() == pytest.approx(0.5)


def test_probability_metrics_and_calibration_bins() -> None:
    target = np.array([0, 0, 1, 1], dtype=np.float64)
    probability = np.array([0.1, 0.4, 0.6, 0.9], dtype=np.float64)
    metrics = probability_metrics(target, probability)
    assert metrics["brier"] == pytest.approx(0.085)
    assert metrics["accuracy"] == 1.0
    assert len(calibration_bins(target, probability)) == 10
    assert 0.0 <= expected_calibration_error(target, probability) <= 1.0


def test_calibration_round_trip_and_transform(tmp_path) -> None:
    calibrator = PlattCalibrator.fit([-2, -1, 1, 2], [0, 0, 1, 1])
    path = tmp_path / "calibration.json"
    calibrator.save(path)
    transformed = PlattCalibrator.load(path).transform_logits([-1, 1])
    assert transformed.shape == (2,)
    assert np.all((0.0 <= transformed) & (transformed <= 1.0))
    assert transformed[0] < transformed[1]


def test_saved_model_loads_with_identical_prediction(tmp_path) -> None:
    preprocessor = fitted_preprocessor()
    config = ModelConfig(input_size=len(V1_FEATURES), dropout=0.0)
    model = WinProbabilityMLP(config).eval()
    torch.save(model.state_dict(), tmp_path / "best_model.pt")
    (tmp_path / "model_config.json").write_text(json.dumps(config.to_dict()), encoding="utf-8")
    preprocessor.save(tmp_path / "preprocessing.json")
    state = model_frame().iloc[1].to_dict()
    expected = WinProbabilityPredictor(model, preprocessor).predict_one(state)
    actual = WinProbabilityPredictor.load(tmp_path).predict_one(state)
    assert actual == pytest.approx(expected)
    assert 0.0 <= actual <= 1.0


def test_inference_checks_feature_presence() -> None:
    predictor = WinProbabilityPredictor(
        WinProbabilityMLP(ModelConfig(input_size=len(V1_FEATURES))), fitted_preprocessor()
    )
    with pytest.raises(KeyError, match="Missing inference features"):
        predictor.predict_one({"score_differential": 0})
