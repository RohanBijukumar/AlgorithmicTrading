"""Inspect artifact provenance and plan strictly chronological, purged evaluation folds."""

from __future__ import annotations

import hashlib
from datetime import date

from .nn_models import MODEL_DIR, MODEL_NAMES, load_model


def audit_models() -> list[dict]:
    required = (
        "training_code_revision",
        "training_data_sha256",
        "training_seed",
        "label_definition",
        "target_transform",
        "split_manifest",
        "last_training_label_date",
        "last_validation_label_date",
    )
    audits = []
    for name in MODEL_NAMES:
        model = load_model(name)
        audits.append(
            {
                "model": name,
                "sha256": hashlib.sha256((MODEL_DIR / f"{name}.json").read_bytes()).hexdigest(),
                "parameter_count": sum(len(row) for row in model["hidden_weights"])
                + len(model["hidden_bias"])
                + len(model["output_weights"])
                + 1,
                "trained_through": model["trained_through"],
                "validation_end": model["validation_end"],
                "declared_first_backtest": model["first_allowed_backtest_start"],
                "label_horizon_sessions": model["target_horizon_days"],
                "missing_provenance": [field for field in required if field not in model],
                "status": "experimental; live viability unestablished",
            }
        )
    return audits


def purged_walk_forward(
    dates: list[date],
    label_horizon: int = 21,
    minimum_training: int = 504,
    validation_size: int = 126,
    test_size: int = 126,
) -> list[dict]:
    """Return index sets over unique sessions, never independently shuffled stock rows.

    Each sample's label ends at index + horizon. Label ends must precede the next
    partition's first feature date. Feature lookback history may cross a boundary.
    """
    if any(
        type(v) is not int or v <= 0
        for v in (label_horizon, minimum_training, validation_size, test_size)
    ):
        raise ValueError("fold sizes and label horizon must be positive integers")
    if dates != sorted(set(dates)):
        raise ValueError("dates must be unique and chronological")
    folds = []
    validation_start = minimum_training + label_horizon
    while validation_start + validation_size + 2 * label_horizon + test_size <= len(dates):
        train_stop = validation_start - label_horizon
        validation_stop = validation_start + validation_size
        test_start = validation_stop + label_horizon
        test_stop = test_start + test_size
        folds.append(
            {
                "train": list(range(train_stop)),
                "validation": list(range(validation_start, validation_stop)),
                "test": list(range(test_start, test_stop)),
            }
        )
        validation_start += test_size
    return folds


def purge_label_overlap(samples: list[dict], boundary: date) -> list[dict]:
    """Keep samples whose entire realized target is known before a partition boundary."""
    return [
        sample for sample in samples if sample["feature_date"] <= sample["label_end"] < boundary
    ]
