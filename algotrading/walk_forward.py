"""Pure-Python inference for precomputed chronological, purged model ensembles."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from math import isfinite, tanh
from pathlib import Path

REGISTRY_PATH = Path(__file__).parent / "models" / "walk_forward_v2.json"


def validate_registry(registry):
    from .nn_models import FEATURE_NAMES

    if registry.get("version") != 2 or registry.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("invalid walk-forward feature schema")
    seen = set()
    for fold in registry["folds"]:
        year = fold["test_year"]
        if (
            year in seen
            or fold["effective_from"] != f"{year}-01-01"
            or fold["effective_to"] != f"{year}-12-31"
        ):
            raise ValueError("invalid/duplicate walk-forward fold")
        seen.add(year)
        if len(fold["members"]) != 3:
            raise ValueError("walk-forward fold requires three ensemble members")
        for member in fold["members"]:
            if not (
                member["trained_from"]
                <= member["trained_through"]
                <= member["last_training_label_date"]
                < member["validation_start"]
                <= member["validation_end"]
                <= member["last_validation_label_date"]
                < fold["effective_from"]
            ):
                raise ValueError("walk-forward label leakage across fold boundary")
            if (
                len(member["mean"]) != 7
                or len(member["scale"]) != 7
                or any(v <= 0 for v in member["scale"])
            ):
                raise ValueError("invalid fold normalization")
            if (
                len(member["hidden_weights"]) != 12
                or len(member["hidden_bias"]) != 12
                or any(len(r) != 7 for r in member["hidden_weights"])
            ):
                raise ValueError("invalid fold hidden dimensions")
            if (
                len(member["output_weights"]) != 2
                or len(member["output_bias"]) != 2
                or any(len(r) != 12 for r in member["output_weights"])
            ):
                raise ValueError("invalid fold output dimensions")
            values = (
                member["mean"]
                + member["scale"]
                + member["hidden_bias"]
                + member["output_bias"]
                + [v for r in member["hidden_weights"] + member["output_weights"] for v in r]
            )
            if not all(isinstance(v, (int, float)) and isfinite(v) for v in values):
                raise ValueError("nonfinite neural parameters")


@lru_cache(maxsize=4)
def _load(path, modified):
    data = Path(path).read_bytes()
    registry = json.loads(data)
    validate_registry(registry)
    registry["_sha256"] = hashlib.sha256(data).hexdigest()
    return registry


def load_registry():
    if not REGISTRY_PATH.exists():
        return None
    return _load(str(REGISTRY_PATH), REGISTRY_PATH.stat().st_mtime_ns)


def fold_for_date(as_of, registry=None):
    registry = load_registry() if registry is None else registry
    if not registry:
        return None
    # Never borrow a later-trained fold or extend a fold past its tested year.
    return next((f for f in registry["folds"] if f["test_year"] == as_of.year), None)


def predict_member(member, features):
    x = [max(-5, min(5, (v - m) / s)) for v, m, s in zip(features, member["mean"], member["scale"])]
    hidden = [
        tanh(sum(w * v for w, v in zip(row, x)) + bias)
        for row, bias in zip(member["hidden_weights"], member["hidden_bias"])
    ]
    return [
        max(-0.5, min(0.5, (sum(w * v for w, v in zip(row, hidden)) + bias) / 10))
        for row, bias in zip(member["output_weights"], member["output_bias"])
    ]


def score(model_name, features, as_of, registry=None):
    fold = fold_for_date(as_of, registry)
    if fold is None:
        return None
    head = 1 if model_name == "nn_sector_rotation_v1" else 0
    return sum(predict_member(m, features)[head] for m in fold["members"]) / len(fold["members"])


def audit_registry(registry=None):
    registry = load_registry() if registry is None else registry
    if not registry:
        return {
            "model": "walk_forward_v2",
            "status": "not trained; neural signals stay in cash",
            "fold_years": [],
        }
    return {
        "model": "walk_forward_v2",
        "sha256": registry["_sha256"],
        "status": "purged chronological CV; experimental, live viability unestablished",
        "fold_years": [f["test_year"] for f in registry["folds"]],
        "training_code_sha256": registry["training_code_sha256"],
        "dataset_sha256": registry["dataset_sha256"],
        "protocol": registry["protocol"],
        "regularization": registry["regularization"],
        "symbol_count": len(registry["symbols"]),
        "sample_count": registry["sample_count"],
        "created_at": registry["created_at"],
        "limitations": registry["limitations"],
    }
