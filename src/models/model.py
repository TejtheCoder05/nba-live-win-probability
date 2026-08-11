"""Small feed-forward network that emits one unbounded home-win logit."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn


@dataclass(frozen=True)
class ModelConfig:
    input_size: int
    hidden_dimensions: tuple[int, int] = (64, 32)
    dropout: float = 0.1

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["hidden_dimensions"] = list(self.hidden_dimensions)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ModelConfig":
        return cls(
            input_size=int(payload["input_size"]),
            hidden_dimensions=tuple(int(value) for value in payload["hidden_dimensions"]),
            dropout=float(payload["dropout"]),
        )


class WinProbabilityMLP(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        first, second = config.hidden_dimensions
        self.config = config
        self.layers = nn.Sequential(
            nn.Linear(config.input_size, first),
            nn.ReLU(),
            nn.Dropout(config.dropout),
            nn.Linear(first, second),
            nn.ReLU(),
            nn.Linear(second, 1),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.layers(features).squeeze(-1)

    @property
    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())


def logits_to_probabilities(logits: torch.Tensor) -> torch.Tensor:
    return torch.sigmoid(logits)
