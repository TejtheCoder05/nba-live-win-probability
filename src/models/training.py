"""Deterministic CPU/MPS training helpers with validation checkpointing."""

from __future__ import annotations

import copy
import random
from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from src.models.metrics import probability_metrics
from src.models.model import ModelConfig, WinProbabilityMLP


@dataclass(frozen=True)
class TrainingConfig:
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    batch_size: int = 4096
    maximum_epochs: int = 25
    patience: int = 4
    seed: int = 42

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class TrainingResult:
    model: WinProbabilityMLP
    history: list[dict[str, float | int]]
    best_epoch: int
    stopped_epoch: int
    device: str


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.backends.mps.is_available():
        torch.mps.manual_seed(seed)


def select_device() -> torch.device:
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


def make_loader(
    features: np.ndarray,
    target: np.ndarray | None = None,
    weights: np.ndarray | None = None,
    *,
    batch_size: int,
    shuffle: bool,
    seed: int = 42,
) -> DataLoader:
    tensors = [torch.from_numpy(np.asarray(features, dtype=np.float32))]
    if target is not None:
        tensors.append(torch.from_numpy(np.asarray(target, dtype=np.float32)))
    if weights is not None:
        tensors.append(torch.from_numpy(np.asarray(weights, dtype=np.float32)))
    generator = torch.Generator().manual_seed(seed)
    return DataLoader(
        TensorDataset(*tensors),
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=0,
        pin_memory=False,
        generator=generator,
    )


def predict_logits(
    model: WinProbabilityMLP,
    features: np.ndarray,
    *,
    batch_size: int = 16_384,
    device: torch.device | None = None,
) -> np.ndarray:
    actual_device = device or next(model.parameters()).device
    model.eval()
    batches: list[np.ndarray] = []
    loader = make_loader(features, batch_size=batch_size, shuffle=False)
    with torch.inference_mode():
        for (batch,) in loader:
            batches.append(model(batch.to(actual_device)).detach().cpu().numpy())
    return np.concatenate(batches).astype(np.float64, copy=False)


def train_model(
    train_features: np.ndarray,
    train_target: np.ndarray,
    validation_features: np.ndarray,
    validation_target: np.ndarray,
    *,
    model_config: ModelConfig,
    training_config: TrainingConfig,
    sample_weights: np.ndarray | None = None,
    verbose: bool = True,
) -> TrainingResult:
    seed_everything(training_config.seed)
    device = select_device()
    model = WinProbabilityMLP(model_config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=training_config.learning_rate, weight_decay=training_config.weight_decay
    )
    loss_function = nn.BCEWithLogitsLoss(reduction="none")
    loader = make_loader(
        train_features,
        train_target,
        sample_weights,
        batch_size=training_config.batch_size,
        shuffle=True,
        seed=training_config.seed,
    )
    best_state: dict[str, torch.Tensor] | None = None
    best_brier = float("inf")
    best_epoch = 0
    stale_epochs = 0
    history: list[dict[str, float | int]] = []

    for epoch in range(1, training_config.maximum_epochs + 1):
        model.train()
        total_loss = 0.0
        total_rows = 0
        for batch in loader:
            batch_features, batch_target = batch[:2]
            batch_weight = batch[2] if len(batch) == 3 else None
            batch_features = batch_features.to(device)
            batch_target = batch_target.to(device)
            optimizer.zero_grad(set_to_none=True)
            losses = loss_function(model(batch_features), batch_target)
            loss = (losses * batch_weight.to(device)).mean() if batch_weight is not None else losses.mean()
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach().cpu()) * len(batch_target)
            total_rows += len(batch_target)

        validation_logits = predict_logits(model, validation_features, device=device)
        validation_probability = 1.0 / (1.0 + np.exp(-np.clip(validation_logits, -50.0, 50.0)))
        metrics = probability_metrics(validation_target, validation_probability)
        row: dict[str, float | int] = {
            "epoch": epoch,
            "train_loss": total_loss / total_rows,
            "validation_brier": metrics["brier"],
            "validation_log_loss": metrics["log_loss"],
        }
        history.append(row)
        if verbose:
            print(
                f"epoch={epoch:02d} train_loss={row['train_loss']:.6f} "
                f"val_brier={row['validation_brier']:.6f} val_log_loss={row['validation_log_loss']:.6f}"
            )
        if metrics["brier"] < best_brier - 1e-7:
            best_brier = metrics["brier"]
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= training_config.patience:
                break

    if best_state is None:
        raise RuntimeError("Training did not produce a checkpoint")
    model.load_state_dict(best_state)
    return TrainingResult(
        model=model,
        history=history,
        best_epoch=best_epoch,
        stopped_epoch=int(history[-1]["epoch"]),
        device=str(device),
    )
