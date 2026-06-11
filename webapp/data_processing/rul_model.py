"""RUL classifier model — vendored from the standalone `battery_estimation`
project (train_clf.py).

The web app only ever runs **inference**, so this file deliberately contains
just the network definition and the class metadata. None of the training code
(CMA-ES, optimisers, dataloaders) is imported, which keeps the web app free of
the `cma` dependency.

Architecture must match the trained checkpoints exactly:
    CNN (1->16->32->cnn_dim) over each cycle's dQ curve (1000 bins)
    + a summary-feature projection
    -> bidirectional GRU over the cycle sequence
    -> linear head producing `n_classes` logits.
"""

from __future__ import annotations

import torch
import torch.nn as nn

# 5-class RUL scheme (identical to the training project).
N_CLASSES = 5
CLASS_NAMES = ["RUL>400", "RUL 300-400", "RUL 200-300", "RUL 100-200", "RUL<100"]
CLASS_COLORS = ["#1565C0", "#2E7D32", "#F9A825", "#E65100", "#B71C1C"]


class BatteryRULClassifier(nn.Module):
    """CNN + GRU classifier. Defaults match the BML checkpoints
    (cnn_dim=32, gru_dim=32, 2 GRU layers). ``summary_feats`` is read from the
    saved scaler at load time, so it adapts to MIT (16) vs BML (12) models."""

    def __init__(
        self,
        cnn_dim: int = 32,
        gru_dim: int = 32,
        gru_layers: int = 2,
        summary_feats: int = 16,
        n_classes: int = N_CLASSES,
        dropout: float = 0.1,
    ):
        super().__init__()

        self.cnn = nn.Sequential(
            nn.Conv1d(1, 16, kernel_size=5, padding=2), nn.BatchNorm1d(16), nn.ELU(), nn.Dropout(dropout),
            nn.MaxPool1d(2),
            nn.Conv1d(16, 32, kernel_size=5, padding=2), nn.BatchNorm1d(32), nn.ELU(), nn.Dropout(dropout),
            nn.MaxPool1d(2),
            nn.Conv1d(32, cnn_dim, kernel_size=5, padding=2), nn.BatchNorm1d(cnn_dim), nn.ELU(), nn.Dropout(dropout),
            nn.AdaptiveAvgPool1d(1),
        )

        self.summary_proj = nn.Sequential(
            nn.Linear(summary_feats, cnn_dim),
            nn.ELU(),
            nn.Dropout(dropout),
        )

        self.gru = nn.GRU(
            input_size=cnn_dim + cnn_dim,
            hidden_size=gru_dim,
            num_layers=gru_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if gru_layers > 1 else 0.0,
        )

        self.post_gru_drop = nn.Dropout(dropout)

        self.head = nn.Sequential(
            nn.Linear(gru_dim * 2, 32), nn.ELU(), nn.Dropout(dropout),
            nn.Linear(32, n_classes),
        )

    def forward(self, dq: torch.Tensor, summary: torch.Tensor) -> torch.Tensor:
        B, T, C, F = dq.shape

        dq_feat = self.cnn(dq.reshape(B * T, C, F)).squeeze(-1).reshape(B, T, -1)
        summary_feat = self.summary_proj(summary)

        fused = self.post_gru_drop(torch.cat([dq_feat, summary_feat], dim=-1))
        out, h_n = self.gru(fused)
        h_last = self.post_gru_drop(torch.cat([h_n[-2], h_n[-1]], dim=-1))

        return self.head(h_last)
