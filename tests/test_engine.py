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

    def test_costs_and_attribution_reconcile(self):
        result = self.run_strategy(
            "momentum",
            {
                "lookback_days": 5,
                "rebalance_days": 5,
                "top_n": 1,
                "commission": 2.5,
                "slippage_bps": 12.5,
            },
            symbols=["AAPL", "MSFT"],
        )
        report = backtest_report(self.db, result.run_id)
        audit = PortfolioService(self.db).audit(result.portfolio_name, result.end)
        self.assertTrue(audit["reconciled"])
        self.assertAlmostEqual(result.metrics["fees"], len(report["trades"]) * 2.5)
        self.assertAlmostEqual(
            sum(p["total_pnl"] for p in report["run"]["result_summary"]["attribution"]),
            result.metrics["end_value"] - 10000,
        )
        self.assertGreater(result.metrics["slippage"], 0)
        self.assertTrue(all(p["cash"] >= 0 for p in report["equity"]))

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

    def test_new_strategies_run_and_respect_cash_cap(self):
        for strategy in ("dual-momentum", "time-series-momentum", "inverse-volatility"):
            with self.subTest(strategy=strategy):
                params = {
                    "lookback_days": 10,
                    "rebalance_days": 5,
                    "max_position_pct": 25,
                    "cash_reserve_pct": 10,
                }
                if strategy == "dual-momentum":
                    params["skip_days"] = 2
                result = self.run_strategy(strategy, params)
                self.assertGreater(result.trades_executed, 0)
                self.assertGreater(result.metrics["cash_pct"], 0.65)
                self.assertTrue(
                    PortfolioService(self.db).audit(result.portfolio_name, result.end)["reconciled"]
                )

    def test_prestart_history_warms_up_signals(self):
        result = self.service.run(
            "momentum",
            ["AAPL"],
            date(2024, 2, 1),
            date(2024, 2, 20),
            10000,
            {"lookback_days": 5, "rebalance_days": 21},
        )
        trade = self.db.get_trades(self.db.get_portfolio(result.portfolio_name)["id"])[0]
        self.assertEqual(trade.execution_date, date(2024, 2, 2))

    def test_hybrid_is_reproducible(self):
        params = {"lookback_days": 5, "rebalance_days": 5, "top_n": 1, "seed": 43}
        a = self.run_strategy("hybrid", params, ["AAPL", "MSFT"])
        b = self.run_strategy("hybrid", params, ["AAPL", "MSFT"])
        self.assertEqual(a.metrics, b.metrics)
        self.assertEqual(a.trades_executed, b.trades_executed)

    pass

    pass

    pass

    pass

    pass

    pass


class LedgerSafetyTests(unittest.TestCase):
    def test_retroactive_and_non_session_trades_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_backtest_db(Path(tmp))
            service = PortfolioService(db)
            service.create("manual", 1000)
            service.buy("manual", "AAPL", 1, date(2024, 1, 4))
            with self.assertRaisesRegex(ValueError, "backdated"):
                service.buy("manual", "AAPL", 1, date(2024, 1, 2))
            with self.assertRaises(LookupError):
                service.buy("manual", "AAPL", 1, date(2025, 1, 1))
            with self.assertRaisesRegex(ValueError, "positive integer"):
                service.buy("manual", "AAPL", 1.5, date(2024, 1, 5))

    def test_concurrent_buys_cannot_double_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_backtest_db(Path(tmp))
            service = PortfolioService(db)
            service.create("manual", 100)

            def buy(_):
                try:
                    service.buy("manual", "AAPL", 1, date(2024, 1, 2))
                    return True
                except ValueError:
                    return False

            with ThreadPoolExecutor(2) as pool:
                outcomes = list(pool.map(buy, range(2)))
            self.assertEqual(sum(outcomes), 1)
            self.assertEqual(service.state("manual").cash, 0)

    def test_trading_session_metrics_include_first_loss(self):
        metrics = performance_metrics([{"total_value": 90}, {"total_value": 99}], 100)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.1)
        self.assertAlmostEqual(metrics["total_return"], -0.01)
        self.assertEqual(metrics["trading_days"], 2)
        self.assertIsNone(metrics["cagr"])
        self.assertIsNone(performance_metrics([{"total_value": 100}] * 3, 100)["sharpe"])

    pass


if __name__ == "__main__":
    unittest.main()
