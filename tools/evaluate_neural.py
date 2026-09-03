"""Fixed-window neural evaluation and cost stress tests, never a parameter optimizer."""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from algotrading.backtest import BacktestService
from algotrading.db import Database
from algotrading.portfolio import PortfolioService
from algotrading.reporting import backtest_report
from algotrading.walk_forward import audit_registry


def excess_interval(equity, starting_cash, trials=1000, block=21):
    pairs = [
        (p["total_value"], p["benchmark_value"])
        for p in equity
        if p.get("benchmark_value") is not None
    ]
    if len(pairs) != len(equity) or len(pairs) < block * 2:
        return None
    previous = (starting_cash, starting_cash)
    excess = []
    for portfolio, benchmark in pairs:
        excess.append(portfolio / previous[0] - benchmark / previous[1])
        previous = (portfolio, benchmark)
    rng = random.Random(42)
    means = []
    for _ in range(trials):
        draws = []
        while len(draws) < len(excess):
            start = rng.randrange(len(excess))
            draws.extend(excess[(start + i) % len(excess)] for i in range(block))
        means.append(sum(draws[: len(excess)]) / len(excess) * 252)
    means.sort()
    return {
        "low": means[int(trials * 0.05)],
        "high": means[int(trials * 0.95)],
        "definition": "90% circular-block interval for annualized mean daily excess return; exploratory, no multiple-testing correction",
        "block_sessions": block,
        "seed": 42,
        "bootstrap_trials": trials,
    }


def evaluate(db_path, output):
    with tempfile.TemporaryDirectory(prefix="algotrading-neural-") as tmp:
        path = Path(tmp) / "evaluation.sqlite3"
        with (
            sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as source,
            sqlite3.connect(path) as target,
        ):
            source.backup(target)
        db = Database(path)
        db.initialize()
        coverage = db.get_coverage("SPY")
        if not coverage:
            raise LookupError("SPY data is required for aligned evaluation")
        latest = min(coverage[1], date(2026, 12, 31))
        windows = [
            (date(2024, 1, 1), date(2024, 12, 31)),
            (date(2025, 1, 1), date(2025, 12, 31)),
            (date(2026, 1, 1), latest),
        ]
        strategies = (
            "buy-and-hold",
            "momentum",
            "nn-pattern",
            "nn-sector-rotation",
            "nn-risk-adjusted",
        )
        results = []
        for start, end in windows:
            if end <= start or end > latest:
                continue
            for strategy in strategies:
                for stress in [False, True] if strategy.startswith("nn-") else [False]:
                    costs = (
                        {"commission": 2, "slippage_bps": 25}
                        if stress
                        else {"commission": 1, "slippage_bps": 5}
                    )
                    begin = perf_counter()
                    result = BacktestService(db).run(
                        strategy,
                        ["SPY"] if strategy == "buy-and-hold" else ["@universe"],
                        start,
                        end,
                        100000,
                        costs,
                    )
                    report = backtest_report(db, result.run_id)
                    item = {
                        "strategy": strategy,
                        "from": str(start),
                        "to": str(end),
                        "cost_stress": stress,
                        "seconds": round(perf_counter() - begin, 3),
                        "metrics": result.metrics,
                        "benchmark": result.benchmark,
                        "parameters": report["run"]["parameters"],
                        "reconciled": PortfolioService(db).audit(result.portfolio_name, end)[
                            "reconciled"
                        ],
                        "largest_contributors": report["run"]["result_summary"]["attribution"][:5],
                        "excess_interval": excess_interval(report["equity"], 100000),
                        "warnings": report["run"]["result_summary"]["warnings"],
                    }
                    results.append(item)
                    print(
                        f"{start.year} {strategy:20} {'stress' if stress else 'base':6} return {result.metrics['total_return']:+.2%} drawdown {result.metrics['max_drawdown']:.2%}",
                        flush=True,
                    )
        payload = {
            "method": "Frozen artifacts, fixed defaults, independent yearly windows, no retraining or selection. All periods are exploratory, not certified untouched holdouts.",
            "benchmark_last_date": str(latest),
            "models": [audit_registry()],
            "results": results,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, default=str, allow_nan=False), encoding="utf-8"
        )
        print(f"Saved {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/neural_evaluation_v2.json"))
    args = parser.parse_args()
    evaluate(args.db, args.output)
