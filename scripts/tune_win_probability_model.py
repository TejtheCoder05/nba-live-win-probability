"""Tune logistic/MLP models on 2021-22/2022-23 without loading test data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.calibration import PlattCalibrator  # noqa: E402
from src.models.dataset import (  # noqa: E402
    TRAIN_SEASON,
    V1_FEATURES,
    assert_isolated_splits,
    features_and_target,
    make_split,
)
from src.models.metrics import probability_metrics  # noqa: E402
from src.models.model import ModelConfig  # noqa: E402
from src.models.preprocessing import FeaturePreprocessor  # noqa: E402
from src.models.training import TrainingConfig, predict_logits, train_model  # noqa: E402
from src.paths import MODEL_ARTIFACT_DIR, ensure_dir  # noqa: E402


def sigmoid(logits: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(logits, -50.0, 50.0)))


def main() -> int:
    artifact_directory = ensure_dir(MODEL_ARTIFACT_DIR)
    print("Loading training and validation only; 2023-24 test is not opened by this script.")
    train = make_split("train")
    validation = make_split("validation")
    assert_isolated_splits([train, validation])
    train_features_frame, train_target = features_and_target(train.frame)
    validation_features_frame, validation_target = features_and_target(validation.frame)
    preprocessor = FeaturePreprocessor().fit(train_features_frame, season=TRAIN_SEASON)
    train_features = preprocessor.transform(train_features_frame)
    validation_features = preprocessor.transform(validation_features_frame)

    logistic = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500, random_state=42)
    logistic.fit(train_features, train_target)
    logistic_probability = logistic.predict_proba(validation_features)[:, 1]
    logistic_artifact = {
        "model": "logistic_regression",
        "c": 1.0,
        "feature_names": list(V1_FEATURES),
        "coefficient": logistic.coef_[0].tolist(),
        "intercept": float(logistic.intercept_[0]),
        "validation_metrics": probability_metrics(validation_target, logistic_probability),
    }

    candidates = [
        (
            "mlp_64_32_dropout_0.1_lr_0.001",
            ModelConfig(input_size=len(V1_FEATURES), hidden_dimensions=(64, 32), dropout=0.1),
            TrainingConfig(learning_rate=1e-3),
        ),
        (
            "mlp_64_32_dropout_0.2_lr_0.001",
            ModelConfig(input_size=len(V1_FEATURES), hidden_dimensions=(64, 32), dropout=0.2),
            TrainingConfig(learning_rate=1e-3),
        ),
        (
            "mlp_64_32_dropout_0.1_lr_0.0003",
            ModelConfig(input_size=len(V1_FEATURES), hidden_dimensions=(64, 32), dropout=0.1),
            TrainingConfig(learning_rate=3e-4),
        ),
    ]
    results: list[dict[str, object]] = []
    trained = {}
    for name, model_config, training_config in candidates:
        print(f"\nTraining {name} on {train_features.shape[0]:,} rows")
        result = train_model(
            train_features,
            train_target,
            validation_features,
            validation_target,
            model_config=model_config,
            training_config=training_config,
        )
        logits = predict_logits(result.model, validation_features)
        metrics = probability_metrics(validation_target, sigmoid(logits))
        row = {
            "name": name,
            "model_config": model_config.to_dict(),
            "training_config": training_config.to_dict(),
            "parameter_count": result.model.parameter_count,
            "device": result.device,
            "best_epoch": result.best_epoch,
            "stopped_epoch": result.stopped_epoch,
            "validation_metrics": metrics,
            "history": result.history,
        }
        results.append(row)
        trained[name] = (result, logits, model_config, training_config)

    best = min(results, key=lambda row: (row["validation_metrics"]["brier"], row["validation_metrics"]["log_loss"]))
    best_result, best_logits, best_model_config, best_training_config = trained[best["name"]]

    # Calibration gets an honest validation-only assessment: early validation
    # games fit Platt scaling and later validation games assess whether to use it.
    ordered_games = np.array(sorted(validation.frame["game_id"].astype(str).unique()))
    calibration_games = set(ordered_games[: len(ordered_games) // 2])
    calibration_fit_mask = validation.frame["game_id"].astype(str).isin(calibration_games).to_numpy()
    calibration_assessment_mask = ~calibration_fit_mask
    assessment_raw = probability_metrics(
        validation_target[calibration_assessment_mask], sigmoid(best_logits[calibration_assessment_mask])
    )
    assessment_calibrator = PlattCalibrator.fit(
        best_logits[calibration_fit_mask], validation_target[calibration_fit_mask]
    )
    assessment_calibrated_probability = assessment_calibrator.transform_logits(
        best_logits[calibration_assessment_mask]
    )
    assessment_calibrated = probability_metrics(
        validation_target[calibration_assessment_mask], assessment_calibrated_probability
    )
    use_calibration = (
        assessment_calibrated["brier"] < assessment_raw["brier"]
        and assessment_calibrated["log_loss"] < assessment_raw["log_loss"]
    )
    final_calibrator = PlattCalibrator.fit(best_logits, validation_target) if use_calibration else None

    preprocessor.save(artifact_directory / "preprocessing.json")
    torch.save(best_result.model.to("cpu").state_dict(), artifact_directory / "best_model.pt")
    (artifact_directory / "model_config.json").write_text(
        json.dumps(best_model_config.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (artifact_directory / "logistic_baseline.json").write_text(
        json.dumps(logistic_artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if final_calibrator is not None:
        final_calibrator.save(artifact_directory / "calibration.json")

    tuning_report = {
        "selection_data": {"train": "2021-22", "validation": "2022-23", "test": "sealed"},
        "constant_probability": float(train_target.mean()),
        "logistic": logistic_artifact,
        "candidates": results,
        "selected_candidate": best["name"],
        "selected_training_config": best_training_config.to_dict(),
        "calibration": {
            "method_considered": "platt",
            "fit_games": len(calibration_games),
            "assessment_games": len(ordered_games) - len(calibration_games),
            "assessment_raw": assessment_raw,
            "assessment_calibrated": assessment_calibrated,
            "applied": use_calibration,
            "final_fit_split": "full validation" if use_calibration else None,
        },
    }
    (artifact_directory / "tuning_report.json").write_text(
        json.dumps(tuning_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("\nSelection summary")
    print(json.dumps({key: tuning_report[key] for key in ("selected_candidate", "calibration")}, indent=2))
    print(f"Artifacts staged in {artifact_directory}; test remains sealed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
