"""Audit Phase 4 data and run train/validation-only logistic comparisons."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.dataset import (  # noqa: E402
    TRAIN_SEASON,
    VALIDATION_SEASON,
    V1_FEATURES,
    assert_isolated_splits,
    dataset_summary,
    features_and_target,
    game_balanced_weights,
    make_split,
    state_distribution,
    time_bucket_sample,
)
from src.models.metrics import probability_metrics  # noqa: E402
from src.models.preprocessing import FeaturePreprocessor  # noqa: E402
from src.paths import MODEL_ARTIFACT_DIR, ensure_dir  # noqa: E402


def fit_logistic(
    train_frame,
    validation_frame,
    *,
    feature_names: tuple[str, ...],
    drop_unknown: bool = False,
    weighting: str = "full",
    c_value: float = 1.0,
) -> dict[str, object]:
    working_train = time_bucket_sample(train_frame) if weighting == "time_bucket_30s" else train_frame
    train_x_frame, train_y = features_and_target(
        working_train, features=feature_names, drop_unknown_possession=drop_unknown
    )
    validation_x_frame, validation_y = features_and_target(
        validation_frame, features=feature_names, drop_unknown_possession=drop_unknown
    )
    preprocessor = FeaturePreprocessor(feature_names=feature_names).fit(train_x_frame, season=TRAIN_SEASON)
    train_x = preprocessor.transform(train_x_frame)
    validation_x = preprocessor.transform(validation_x_frame)
    weights = game_balanced_weights(working_train.loc[train_x_frame.index]) if weighting == "game_balanced" else None
    model = LogisticRegression(C=c_value, solver="lbfgs", max_iter=500, random_state=42)
    model.fit(train_x, train_y, sample_weight=weights)
    probability = model.predict_proba(validation_x)[:, 1]
    return {
        "features": list(feature_names),
        "drop_unknown_possession": drop_unknown,
        "weighting": weighting,
        "c": c_value,
        "train_rows": int(len(train_x)),
        "validation_rows": int(len(validation_x)),
        "metrics": probability_metrics(validation_y, probability),
    }


def main() -> int:
    output_directory = ensure_dir(MODEL_ARTIFACT_DIR)
    print("Loading train and validation seasons only (test remains sealed for model selection)...")
    train = make_split("train")
    validation = make_split("validation")
    assert_isolated_splits([train, validation])

    summaries = {
        "train": dataset_summary(train.frame),
        "validation": dataset_summary(validation.frame),
    }
    distributions = {
        "train": state_distribution(train.frame),
        "validation": state_distribution(validation.frame),
    }
    constant_probability = float(train.frame["home_win"].mean())
    constant_metrics = probability_metrics(
        validation.frame["home_win"], np.full(len(validation.frame), constant_probability)
    )

    experiments: list[dict[str, object]] = []
    base = tuple(V1_FEATURES)
    with_scores = (*base, "home_score", "away_score")
    for features in (base, with_scores):
        experiments.append(fit_logistic(train.frame, validation.frame, feature_names=features))
    for drop_unknown in (False, True):
        experiments.append(
            fit_logistic(train.frame, validation.frame, feature_names=base, drop_unknown=drop_unknown)
        )
    for weighting in ("full", "game_balanced", "time_bucket_30s"):
        experiments.append(fit_logistic(train.frame, validation.frame, feature_names=base, weighting=weighting))
    for c_value in (0.1, 1.0, 10.0):
        experiments.append(fit_logistic(train.frame, validation.frame, feature_names=base, c_value=c_value))

    # Duplicate configurations make the comparison groups explicit in the report;
    # retain one copy in the compact experiment list.
    unique: dict[str, dict[str, object]] = {}
    for experiment in experiments:
        key = json.dumps(
            {name: experiment[name] for name in ("features", "drop_unknown_possession", "weighting", "c")},
            sort_keys=True,
        )
        unique[key] = experiment

    report = {
        "split_policy": {"train": TRAIN_SEASON, "validation": VALIDATION_SEASON, "test": "sealed"},
        "summaries": summaries,
        "distributions": distributions,
        "constant_baseline": {"probability": constant_probability, "validation_metrics": constant_metrics},
        "logistic_experiments": list(unique.values()),
    }
    path = output_directory / "phase4_validation_analysis.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
