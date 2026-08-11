"""One-shot evaluation of frozen artifacts on the untouched 2023-24 season."""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.dataset import (  # noqa: E402
    TEST_SEASON,
    V1_FEATURES,
    dataset_summary,
    features_and_target,
    make_split,
)
from src.models.inference import WinProbabilityPredictor  # noqa: E402
from src.models.metrics import calibration_bins, probability_metrics, time_slice_metrics  # noqa: E402
from src.models.preprocessing import FeaturePreprocessor  # noqa: E402
from src.models.training import predict_logits  # noqa: E402
from src.paths import MODEL_ARTIFACT_DIR  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def logistic_probability(features: np.ndarray, artifact: dict[str, object]) -> np.ndarray:
    coefficient = np.asarray(artifact["coefficient"], dtype=np.float64)
    logits = features @ coefficient + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-np.clip(logits, -50.0, 50.0)))


def select_trajectory_games(frame: pd.DataFrame) -> dict[str, str]:
    grouped = frame.groupby("game_id", sort=True, observed=True)
    final_differential = grouped["score_differential"].last()
    maximum_period = grouped["period"].max()
    home_win = grouped["home_win"].first().astype(bool)
    minimum_differential = grouped["score_differential"].min()
    maximum_differential = grouped["score_differential"].max()
    comeback_magnitude = pd.Series(
        np.where(home_win, -minimum_differential, maximum_differential), index=home_win.index
    )
    close_candidates = final_differential.abs().sort_values()
    blowout_candidates = final_differential.abs().sort_values(ascending=False)
    overtime_candidates = maximum_period[maximum_period > 4]
    if overtime_candidates.empty:
        raise RuntimeError("Test split has no overtime game for trajectory audit")
    return {
        "close": str(close_candidates.index[0]),
        "comeback": str(comeback_magnitude.sort_values(ascending=False).index[0]),
        "blowout": str(blowout_candidates.index[0]),
        "overtime": str(overtime_candidates.index[0]),
    }


def trajectory_samples(frame: pd.DataFrame, probability: np.ndarray) -> dict[str, object]:
    working = frame.assign(home_win_probability=probability)
    result: dict[str, object] = {}
    for category, game_id in select_trajectory_games(frame).items():
        game = working.loc[working["game_id"].astype(str) == game_id]
        indices = np.unique(np.linspace(0, len(game) - 1, num=min(20, len(game)), dtype=int))
        sample = game.iloc[indices]
        rows = []
        for row in sample.itertuples(index=False):
            possession = "unknown" if not row.possession_known else ("home" if row.home_possession else "away")
            rows.append(
                {
                    "period": int(row.period),
                    "clock": str(row.clock),
                    "score": f"{int(row.home_score)}-{int(row.away_score)}",
                    "score_differential": int(row.score_differential),
                    "possession": possession,
                    "home_win_probability": float(row.home_win_probability),
                }
            )
        result[category] = {
            "game_id": game_id,
            "home_win": int(game["home_win"].iloc[0]),
            "final_score": f"{int(game['home_score'].iloc[-1])}-{int(game['away_score'].iloc[-1])}",
            "states": rows,
        }
    return result


def benchmark_inference(predictor: WinProbabilityPredictor, states: list[dict[str, object]]) -> dict[str, object]:
    state = states[0]
    batch = (states * (32 // len(states) + 1))[:32]
    for _ in range(100):
        predictor.predict_one(state)
    individual = []
    for _ in range(1000):
        start = time.perf_counter_ns()
        predictor.predict_one(state)
        individual.append((time.perf_counter_ns() - start) / 1_000_000)
    for _ in range(20):
        predictor.predict_batch(batch)
    batch_latencies = []
    start_total = time.perf_counter_ns()
    for _ in range(300):
        start = time.perf_counter_ns()
        predictor.predict_batch(batch)
        batch_latencies.append((time.perf_counter_ns() - start) / 1_000_000)
    elapsed_seconds = (time.perf_counter_ns() - start_total) / 1_000_000_000
    return {
        "warmup_calls": 100,
        "individual": {
            "measurements": len(individual),
            "median_ms": float(np.median(individual)),
            "p95_ms": float(np.percentile(individual, 95)),
            "throughput_states_per_second": float(1000 / (sum(individual) / 1000)),
        },
        "batch": {
            "batch_size": len(batch),
            "measurements": len(batch_latencies),
            "median_ms": float(np.median(batch_latencies)),
            "p95_ms": float(np.percentile(batch_latencies, 95)),
            "throughput_states_per_second": float(300 * len(batch) / elapsed_seconds),
        },
    }


def create_plots(
    bins: list[dict[str, float | int]], trajectories: dict[str, object], output_directory: Path
) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/nba-win-probability-matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    populated = [row for row in bins if row["count"]]
    figure, axis = plt.subplots(figsize=(6, 6))
    axis.plot([0, 1], [0, 1], "--", color="gray", label="perfect calibration")
    axis.plot(
        [row["mean_predicted"] for row in populated],
        [row["observed_rate"] for row in populated],
        marker="o",
        label="MLP",
    )
    axis.set(xlabel="Mean predicted home-win probability", ylabel="Observed home-win rate", xlim=(0, 1), ylim=(0, 1))
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_directory / "reliability.png", dpi=160)
    plt.close(figure)

    figure, axes = plt.subplots(2, 2, figsize=(12, 8), sharey=True)
    for axis, (category, payload) in zip(axes.flat, trajectories.items(), strict=True):
        probabilities = [row["home_win_probability"] for row in payload["states"]]
        axis.plot(range(len(probabilities)), probabilities, marker="o", markersize=3)
        axis.set(title=f"{category}: {payload['game_id']} ({payload['final_score']})", ylim=(0, 1))
        axis.set_xlabel("sampled state sequence")
        axis.set_ylabel("P(home win)")
    figure.tight_layout()
    figure.savefig(output_directory / "game_trajectories.png", dpi=160)
    plt.close(figure)


def main() -> int:
    artifact_directory = MODEL_ARTIFACT_DIR
    required = ["best_model.pt", "model_config.json", "preprocessing.json", "logistic_baseline.json", "tuning_report.json"]
    missing = [name for name in required if not (artifact_directory / name).exists()]
    if missing:
        raise FileNotFoundError(f"Frozen validation artifacts are missing: {missing}")

    print("Opening the frozen 2023-24 test split for its one final evaluation...")
    test = make_split("test")
    if test.season != TEST_SEASON:
        raise RuntimeError("Unexpected test season")
    feature_frame, target = features_and_target(test.frame)
    preprocessor = FeaturePreprocessor.load(artifact_directory / "preprocessing.json")
    features = preprocessor.transform(feature_frame)
    predictor = WinProbabilityPredictor.load(artifact_directory)
    logits = predict_logits(predictor.model, features)
    raw_mlp_probability = 1.0 / (1.0 + np.exp(-np.clip(logits, -50.0, 50.0)))
    final_probability = (
        predictor.calibrator.transform_logits(logits) if predictor.calibrator is not None else raw_mlp_probability
    )
    logistic_artifact = json.loads((artifact_directory / "logistic_baseline.json").read_text(encoding="utf-8"))
    logistic = logistic_probability(features, logistic_artifact)
    tuning_report = json.loads((artifact_directory / "tuning_report.json").read_text(encoding="utf-8"))
    constant = np.full(len(target), float(tuning_report["constant_probability"]))

    comparison = {
        "constant_home_win_rate": probability_metrics(target, constant),
        "logistic_regression": probability_metrics(target, logistic),
        "raw_pytorch_mlp": probability_metrics(target, raw_mlp_probability),
    }
    if predictor.calibrator is not None:
        comparison["calibrated_pytorch_mlp"] = probability_metrics(target, final_probability)
    bins = calibration_bins(target, final_probability)
    slices = time_slice_metrics(test.frame, final_probability)
    trajectories = trajectory_samples(test.frame, final_probability)
    inference_states = test.frame.iloc[:32].loc[:, V1_FEATURES].to_dict(orient="records")
    latency = benchmark_inference(predictor, inference_states)
    create_plots(bins, trajectories, artifact_directory)

    selected = next(
        row for row in tuning_report["candidates"] if row["name"] == tuning_report["selected_candidate"]
    )
    evaluation = {
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "test_season": TEST_SEASON,
        "comparison": comparison,
        "selected_model": "calibrated_pytorch_mlp" if predictor.calibrator else "raw_pytorch_mlp",
    }
    metadata = {
        "artifact_version": 1,
        "target": "home_win",
        "feature_names": list(V1_FEATURES),
        "splits": {"train": "2021-22", "validation": "2022-23", "test": "2023-24"},
        "unknown_possession": {"imputation": 0.5, "indicator": "possession_known", "rows_dropped": False},
        "sampling": "all emitted game states, unweighted",
        "selected_candidate": tuning_report["selected_candidate"],
        "model_config": selected["model_config"],
        "training_config": selected["training_config"],
        "parameter_count": selected["parameter_count"],
        "device": selected["device"],
        "best_epoch": selected["best_epoch"],
        "stopped_epoch": selected["stopped_epoch"],
        "calibration_applied": bool(predictor.calibrator),
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "torch": torch.__version__,
        },
    }
    write_json(artifact_directory / "evaluation_metrics.json", evaluation)
    write_json(artifact_directory / "calibration_bins.json", bins)
    write_json(artifact_directory / "time_slice_metrics.json", slices)
    write_json(artifact_directory / "trajectory_samples.json", trajectories)
    write_json(artifact_directory / "latency.json", latency)
    write_json(artifact_directory / "model_metadata.json", metadata)
    write_json(artifact_directory / "feature_metadata.json", {"ordered_features": list(V1_FEATURES), "target": "home_win"})
    write_json(artifact_directory / "dataset_summary.json", {"test": dataset_summary(test.frame)})
    write_json(artifact_directory / "training_history.json", selected["history"])
    print(json.dumps({"test": evaluation, "latency": latency}, indent=2))
    print(f"Final artifacts written to {artifact_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
