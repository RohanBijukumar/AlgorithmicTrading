"""Offline, date-blinded walk-forward ensembles. PyTorch is training-only."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from .db import Database, utc_now
from .nn_models import FEATURE_NAMES, neural_features, sector_for_symbol

HORIZON = 21
SEEDS = (42, 43, 44)
HISTORY_YEARS = (None, 10, 5)


def split_samples(samples, year, history_years=None):
    """Labels, not merely features, must finish before the following partition."""
    validation_start = date(year - 1, 1, 1)
    test_start = date(year, 1, 1)
    test_end = date(year, 12, 31)
    lower = date(validation_start.year - history_years, 1, 1) if history_years else date.min
    train = [s for s in samples if lower <= s["date"] and s["label_end"] < validation_start]
    validation = [
        s for s in samples if validation_start <= s["date"] and s["label_end"] < test_start
    ]
    test = [
        s for s in samples if test_start <= s["date"] <= test_end and s["label_end"] <= test_end
    ]
    return train, validation, test


def _fingerprint(rows):
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def build_samples(db, start, end, stride=10):
    calendar = db.trading_dates(start, end)
    indices = {d: i for i, d in enumerate(calendar)}
    benchmark = db.get_bars("SPY", start, end)
    benchmark_by_date = {b.trading_date: b for b in benchmark}
    raw = []
    for row in db.list_symbols():
        bars = db.get_bars(row["symbol"], start, end)
        sector = sector_for_symbol(row["symbol"], row["sector"])
        for i in range(63, len(bars) - HORIZON):
            current = bars[i].trading_date
            if indices[current] % stride:
                continue
            # No interpolated IPO history or missing sessions in features/targets.
            if (
                indices[bars[i + HORIZON].trading_date] - indices[bars[i - 63].trading_date]
                != 63 + HORIZON
            ):
                continue
            features = neural_features(bars[i - 63 : i + 1])
            entry, exit_bar = bars[i + 1], bars[i + HORIZON]
            entry_price = entry.open * (entry.adjusted_close or entry.close) / entry.close
            future_return = (exit_bar.adjusted_close or exit_bar.close) / entry_price - 1
            bentry = benchmark_by_date.get(entry.trading_date)
            bexit = benchmark_by_date.get(exit_bar.trading_date)
            market_return = (
                (
                    (bexit.adjusted_close or bexit.close)
                    / (bentry.open * (bentry.adjusted_close or bentry.close) / bentry.close)
                    - 1
                )
                if bentry and bexit
                else None
            )
            raw.append(
                {
                    "date": current,
                    "label_end": exit_bar.trading_date,
                    "symbol": row["symbol"],
                    "sector": sector,
                    "features": features,
                    "future_return": future_return,
                    "market_return": market_return,
                }
            )
    market_groups, sector_groups = defaultdict(list), defaultdict(list)
    for s in raw:
        market_groups[s["date"]].append(s["future_return"])
    for s in raw:
        market = s["market_return"]
        if market is None:
            values = market_groups[s["date"]]
            market = sum(values) / len(values)
        s["excess"] = max(-0.5, min(0.5, s["future_return"] - market))
        sector_groups[(s["date"], s["sector"])].append(s["excess"])
    for s in raw:
        group = sector_groups[(s["date"], s["sector"])]
        s["target"] = [s["excess"] * 10, sum(group) / len(group) * 10]
    return sorted(raw, key=lambda s: (s["date"], s["symbol"]))


def _fit(train, validation, seed, epochs):
    import torch

    torch.manual_seed(seed)
    x = torch.tensor([s["features"] for s in train], dtype=torch.float32)
    y = torch.tensor([s["target"] for s in train], dtype=torch.float32)
    vx = torch.tensor([s["features"] for s in validation], dtype=torch.float32)
    vy = torch.tensor([s["target"] for s in validation], dtype=torch.float32)
    mean, scale = x.mean(0), x.std(0).clamp(min=0.05)
    x, vx = ((x - mean) / scale).clamp(-5, 5), ((vx - mean) / scale).clamp(-5, 5)
    years = Counter(s["date"].year for s in train)
    weights = torch.tensor([1 / years[s["date"].year] for s in train])
    weights /= weights.sum()
    model = torch.nn.Sequential(
        torch.nn.Linear(7, 12), torch.nn.Tanh(), torch.nn.Dropout(0.1), torch.nn.Linear(12, 2)
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01, weight_decay=0.03)
    loss_fn = torch.nn.SmoothL1Loss(reduction="none")
    best, best_loss, best_epoch, patience = None, float("inf"), 0, 0
    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        loss = (loss_fn(model(x), y).mean(1) * weights).sum()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        model.eval()
        with torch.no_grad():
            validation_loss = float(loss_fn(model(vx), vy).mean())
        if validation_loss < best_loss - 0.00001:
            best, best_loss, best_epoch, patience = (
                copy.deepcopy(model.state_dict()),
                validation_loss,
                epoch + 1,
                0,
            )
        else:
            patience += 1
        if patience >= 8:
            break
    model.load_state_dict(best)
    return {
        "seed": seed,
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "hidden_weights": best["0.weight"].tolist(),
        "hidden_bias": best["0.bias"].tolist(),
        "output_weights": best["3.weight"].tolist(),
        "output_bias": best["3.bias"].tolist(),
        "best_epoch": best_epoch,
        "validation_loss": best_loss,
        "training_samples": len(train),
        "validation_samples": len(validation),
        "trained_from": str(train[0]["date"]),
        "trained_through": str(train[-1]["date"]),
        "last_training_label_date": str(max(s["label_end"] for s in train)),
        "validation_start": str(validation[0]["date"]),
        "validation_end": str(validation[-1]["date"]),
        "last_validation_label_date": str(max(s["label_end"] for s in validation)),
        "training_data_sha256": _fingerprint(train),
        "validation_data_sha256": _fingerprint(validation),
    }


def train_walk_forward(db, start, end, first_year, last_year, epochs, output, progress=print):
    import torch

    from .walk_forward import predict_member, validate_registry

    if start >= end or first_year > last_year or not 1 <= epochs <= 500:
        raise ValueError("invalid training dates, fold years, or epochs (1-500)")
    if first_year < 1902 or last_year > end.year + 1:
        raise ValueError("fold years must be within the available historical chronology")
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    progress("Building ticker-blinded, date-grouped features and realized forward labels...")
    with tempfile.TemporaryDirectory(prefix="algotrading-training-") as temporary_dir:
        snapshot = Path(temporary_dir) / "snapshot.sqlite3"
        with db.connect() as source, sqlite3.connect(snapshot) as target:
            source.backup(target)
        samples = build_samples(Database(snapshot), start, end)
    if not samples:
        raise ValueError("No complete training samples; sync older history first")
    folds, skipped = [], []
    for year in range(first_year, last_year + 1):
        members = []
        for seed, years in zip(SEEDS, HISTORY_YEARS):
            train, validation, _ = split_samples(samples, year, years)
            if (
                len({s["date"] for s in train}) < 50
                or len({s["date"] for s in validation}) < 12
                or len(train) < 500
            ):
                break
            member = _fit(train, validation, seed, epochs)
            member["history_years"] = years
            members.append(member)
        if len(members) != len(SEEDS):
            skipped.append(year)
            progress(
                f"{year}: insufficient pre-fold training/validation history; no future-data fallback"
            )
            continue
        # The test partition is first scored after all fitting/early stopping has ended.
        _, _, test = split_samples(samples, year)
        mse = None
        if test:
            error = 0
            for sample in test:
                predictions = [predict_member(m, sample["features"]) for m in members]
                error += (
                    sum(
                        (
                            sum(p[h] for p in predictions) / len(predictions)
                            - sample["target"][h] / 10
                        )
                        ** 2
                        for h in (0, 1)
                    )
                    / 2
                )
            mse = error / len(test)
        fold = {
            "test_year": year,
            "effective_from": f"{year}-01-01",
            "effective_to": f"{year}-12-31",
            "members": members,
            "test_samples": len(test),
            "test_mse": mse,
            "test_data_sha256": _fingerprint(test),
            "test_first_label_date": str(min(s["label_end"] for s in test)) if test else None,
            "test_last_label_date": str(max(s["label_end"] for s in test)) if test else None,
        }
        folds.append(fold)
        progress(
            f"{year}: saved blind test fold, {len(members)} regularized members, {len(test)} held-out samples"
        )
    if not folds:
        raise ValueError(
            "No eligible folds. Download several years before the desired first test year."
        )
    source_hash = hashlib.sha256(
        Path(__file__).read_bytes() + (Path(__file__).parent / "nn_models.py").read_bytes()
    ).hexdigest()
    registry = {
        "version": 2,
        "created_at": utc_now().isoformat(),
        "framework": f"PyTorch {torch.__version__}",
        "feature_names": list(FEATURE_NAMES),
        "target_horizon_sessions": HORIZON,
        "label_definition": "Next-session adjusted open to 21st following session adjusted close; excess over SPY (same-date cross-sectional fallback before SPY). Sector head averages excess by static sector.",
        "target_transform": "clip stock excess to [-0.5,0.5], multiply by 10 for training; divide predictions by 10",
        "training_code_sha256": source_hash,
        "dataset_sha256": _fingerprint(samples),
        "training_range": [str(start), str(end)],
        "sample_count": len(samples),
        "symbols": sorted({s["symbol"] for s in samples}),
        "stride_sessions": 10,
        "protocol": "Annual expanding/10-year/5-year ensembles; prior-year validation; label purging; no test-guided tuning; all symbols on a date stay together",
        "regularization": {
            "weight_decay": 0.03,
            "dropout": 0.1,
            "patience": 8,
            "max_epochs": epochs,
            "gradient_clip": 1,
            "year_balanced_loss": True,
        },
        "limitations": [
            "Current surviving universe and static sectors",
            "Historical as-if training, not proof the procedure existed then",
            "Repeated inspection makes these development results, not a fresh live holdout",
            "Test fold outcomes never choose weights or settings",
        ],
        "skipped_years": skipped,
        "folds": folds,
    }
    validate_registry(registry)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(registry, indent=2, allow_nan=False) + "\n")
    temporary.replace(output)
    progress(f"Saved {len(folds)} folds to {output}; runtime requires no PyTorch or retraining")
    return registry
