"""Evaluate fixed strategy defaults on an isolated copy of an existing database."""

from __future__ import annotations

import argparse
import json
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ", "IWM"])
    parser.add_argument("--cash", type=float, default=100000)
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["buy-and-hold", "dual-momentum", "time-series-momentum", "inverse-volatility"],
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="algotrading-evaluation-") as tmp:
        path = Path(tmp) / "evaluation.sqlite3"
        with (
            sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as source,
            sqlite3.connect(path) as target,
        ):
            source.backup(target)
        db = Database(path)
        for strategy in args.strategies:
            begin = perf_counter()
            result = BacktestService(db).run(
                strategy, args.symbols, args.start, args.end, args.cash
            )
            audit = PortfolioService(db).audit(result.portfolio_name, args.end)
            print(
                json.dumps(
                    {
                        "strategy": strategy,
                        "seconds": round(perf_counter() - begin, 3),
                        "return": round(result.metrics["total_return"], 6),
                        "drawdown": round(result.metrics["max_drawdown"], 6),
                        "sharpe": result.metrics["sharpe"],
                        "trades": result.trades_executed,
                        "fees": result.metrics["fees"],
                        "reconciled": audit["reconciled"],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
