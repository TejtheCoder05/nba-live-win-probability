"""Probability-first evaluation and interpretable game-state slices."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score

PROBABILITY_EPSILON = 1e-7


def probability_metrics(target: Iterable[float], probability: Iterable[float]) -> dict[str, float]:
    target_array = np.asarray(target, dtype=np.float64)
    probability_array = np.asarray(probability, dtype=np.float64)
    if target_array.shape != probability_array.shape:
        raise ValueError("Target and probability shapes differ")
    if not np.isfinite(probability_array).all():
        raise ValueError("Probabilities contain NaN or infinity")
    clipped = np.clip(probability_array, PROBABILITY_EPSILON, 1.0 - PROBABILITY_EPSILON)
    return {
        "brier": float(np.mean(np.square(probability_array - target_array))),
        "log_loss": float(log_loss(target_array, clipped, labels=[0, 1])),
        "accuracy": float(accuracy_score(target_array, probability_array >= 0.5)),
        "roc_auc": float(roc_auc_score(target_array, probability_array)),
        "ece": expected_calibration_error(target_array, probability_array),
    }


def calibration_bins(
    target: Iterable[float], probability: Iterable[float], bins: int = 10
) -> list[dict[str, float | int]]:
    if bins <= 0:
        raise ValueError("bins must be positive")
    target_array = np.asarray(target, dtype=np.float64)
    probability_array = np.asarray(probability, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    assignments = np.minimum(np.searchsorted(edges, probability_array, side="right") - 1, bins - 1)
    assignments = np.maximum(assignments, 0)
    result: list[dict[str, float | int]] = []
    for index in range(bins):
        mask = assignments == index
        count = int(mask.sum())
        result.append(
            {
                "lower": float(edges[index]),
                "upper": float(edges[index + 1]),
                "count": count,
                "mean_predicted": float(probability_array[mask].mean()) if count else 0.0,
                "observed_rate": float(target_array[mask].mean()) if count else 0.0,
                "absolute_gap": (
                    float(abs(probability_array[mask].mean() - target_array[mask].mean())) if count else 0.0
                ),
            }
        )
    return result


def expected_calibration_error(target: Iterable[float], probability: Iterable[float], bins: int = 10) -> float:
    rows = calibration_bins(target, probability, bins=bins)
    total = sum(int(row["count"]) for row in rows)
    if total == 0:
        raise ValueError("Cannot calculate ECE for an empty dataset")
    return float(sum(int(row["count"]) * float(row["absolute_gap"]) for row in rows) / total)


def _slice_metrics(frame: pd.DataFrame, probability: np.ndarray, labels: pd.Series) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for label in labels.cat.categories:
        mask = (labels == label).to_numpy()
        if mask.sum() and frame.loc[mask, "home_win"].nunique() == 2:
            result[str(label)] = {"rows": int(mask.sum()), **probability_metrics(frame.loc[mask, "home_win"], probability[mask])}
    return result


def time_slice_metrics(frame: pd.DataFrame, probability: Iterable[float]) -> dict[str, dict[str, dict[str, float]]]:
    probability_array = np.asarray(probability, dtype=np.float64)
    if len(frame) != len(probability_array):
        raise ValueError("Frame and probability lengths differ")
    quarter = pd.Categorical(
        np.where(frame["period"] > 4, "OT", "Q" + frame["period"].astype(str)),
        categories=["Q1", "Q2", "Q3", "Q4", "OT"],
        ordered=True,
    )
    time_labels = pd.cut(
        frame["seconds_remaining_regulation"],
        bins=[-1, 120, 360, 720, 1440, 2160, np.inf],
        labels=["0-2m", "2-6m", "6-12m", "12-24m", "24-36m", ">36m"],
    )
    score_labels = pd.cut(
        frame["score_differential"].abs(),
        bins=[-1, 2, 5, 10, 20, np.inf],
        labels=["0-2", "3-5", "6-10", "11-20", "20+"],
    )
    return {
        "period": _slice_metrics(frame, probability_array, pd.Series(quarter, index=frame.index)),
        "time_remaining": _slice_metrics(frame, probability_array, time_labels),
        "absolute_score_differential": _slice_metrics(frame, probability_array, score_labels),
    }
