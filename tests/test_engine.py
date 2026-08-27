from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from test_backtest import _bar, seeded_backtest_db

from algotrading.analytics import performance_metrics
from algotrading.backtest import BacktestService
from algotrading.cli import build_parser
from algotrading.jobs import JobStore
from algotrading.market_window import MarketWindow
from algotrading.portfolio import PortfolioService
from algotrading.reporting import backtest_report, export_csv
from algotrading.simulation import BacktestCancelled


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = seeded_backtest_db(Path(self.temp.name))
        self.service = BacktestService(self.db)

    def run_strategy(self, name="buy-and-hold", params=None, symbols=None, progress=None, end=None):
        return self.service.run(
            name,
            symbols or ["AAPL"],
            date(2024, 1, 2),
            end or date(2024, 2, 20),
            10000,
            params,
            progress=progress,
        )

    def test_next_open_fill_cannot_use_same_close(self):
        first = date(2024, 1, 2)
        self.db.insert_market_bars(
            [
                _bar("AAPL", first, 100),
                replace(_bar("AAPL", first + timedelta(days=1), 190), open=200, high=210),
            ]
        )
        result = self.run_strategy(
            params={"commission": 0, "slippage_bps": 0}, end=first + timedelta(days=1)
        )
        trades = self.db.get_trades(self.db.get_portfolio(result.portfolio_name)["id"])
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].execution_date, first + timedelta(days=1))
        self.assertEqual(trades[0].execution_price, 200)
        self.assertEqual(trades[0].shares, 50)
        self.assertAlmostEqual(result.metrics["total_return"], -0.05)

    pass

    pass

    pass

    pass

    pass

    def test_no_fill_on_missing_session(self):
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM market_bars WHERE symbol='AAPL' AND trading_date='2024-01-03'"
            )
        events = []
        result = self.run_strategy(symbols=["AAPL", "MSFT"], progress=events.append)
        self.assertTrue(
            any(e["type"] == "order_rejected" and e["symbol"] == "AAPL" for e in events)
        )
        report = backtest_report(self.db, result.run_id)
        self.assertFalse(any(t["symbol"] == "AAPL" for t in report["trades"]))

    def test_market_window_enforces_frontier_and_resets_after_gap(self):
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM market_bars WHERE symbol='AAPL' AND trading_date='2024-01-05'"
            )
        window = MarketWindow(self.db, ["AAPL", "SPY"], date(2024, 2, 20))
        window.frontier = date(2024, 1, 8)
        self.assertEqual(
            window.get_bars("AAPL", date.min, date.max)[-1].trading_date, window.frontier
        )
        self.assertEqual(
            window.get_bar_on_or_before("AAPL", date.max).trading_date, window.frontier
        )
        self.assertEqual(
            window.contiguous_bars("AAPL", window.frontier)[0].trading_date, date(2024, 1, 6)
        )

    def test_future_price_changes_do_not_change_earlier_fills(self):
        params = {"lookback_days": 5, "rebalance_days": 5, "top_n": 1, "commission": 0}
        first = self.run_strategy("momentum", params, symbols=["AAPL", "MSFT"])
        self.db.insert_market_bars([_bar("MSFT", date(2024, 2, 20), 5000)])
        second = self.run_strategy("momentum", params, symbols=["AAPL", "MSFT"])

        def trades(run):
            return [
                (t.symbol, t.side, t.shares, t.execution_date, t.execution_price)
                for t in self.db.get_trades(self.db.get_portfolio(run.portfolio_name)["id"])
            ]

        self.assertEqual(trades(first), trades(second))

    pass

    pass

    pass

    pass

    pass

    pass

    pass

    pass

    pass


class LedgerSafetyTests(unittest.TestCase):
    pass

    pass

    pass

    pass


if __name__ == "__main__":
    unittest.main()
