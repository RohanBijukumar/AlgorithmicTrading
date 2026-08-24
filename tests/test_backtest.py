from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from algotrading.backtest import BacktestService, StrategyContext, _rebalance_to_symbols
from algotrading.db import Database
from algotrading.models import MarketBar
from algotrading.portfolio import PortfolioService
from algotrading.universe import DEFAULT_SYMBOLS


def seeded_backtest_db(path: Path) -> Database:
    db = Database(path / "test.sqlite3")
    db.initialize()
    bars = []
    start = date(2024, 1, 2)
    for offset in range(70):
        trading_date = start + timedelta(days=offset)
        aapl = 100.0 + offset
        msft = 50.0 + offset * 0.5
        app = 25.0 + offset * 0.8
        spy = 400.0 + offset
        bars.extend(
            [
                _bar("AAPL", trading_date, aapl),
                _bar("MSFT", trading_date, msft),
                _bar("APP", trading_date, app),
                _bar("SPY", trading_date, spy),
            ]
        )
    db.insert_market_bars(bars)
    return db


def seeded_universe_db(path: Path) -> Database:
    db = Database(path / "test.sqlite3")
    db.initialize()
    bars = []
    start = date(2024, 1, 2)
    for symbol_index, symbol in enumerate(DEFAULT_SYMBOLS):
        base = 20.0 + symbol_index
        for offset in range(10):
            trading_date = start + timedelta(days=offset)
            price = base + offset
            bars.append(_bar(symbol, trading_date, price))
    db.insert_market_bars(bars)
    return db


def _bar(symbol: str, trading_date: date, price: float) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        trading_date=trading_date,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        adjusted_close=price,
        volume=1000,
        provider="yahoo",
        price_basis="adjusted_close",
    )


class BacktestServiceTests(unittest.TestCase):
    def test_rebalance_reinvests_proceeds_from_symbols_leaving_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            db.initialize()
            first = date(2024, 1, 2)
            second = date(2024, 1, 3)
            db.insert_market_bars(
                [
                    _bar("AAPL", first, 100.0),
                    _bar("AAPL", second, 100.0),
                    _bar("MSFT", first, 50.0),
                    _bar("MSFT", second, 50.0),
                ]
            )
            portfolios = PortfolioService(db)
            portfolios.create("rotation", 1000.0)
            portfolios.buy("rotation", "AAPL", 10, first)
            context = StrategyContext(
                db=db,
                portfolio=portfolios,
                portfolio_name="rotation",
                current_date=second,
                symbols=["AAPL", "MSFT"],
                start=first,
                end=second,
                parameters={},
            )

            orders = _rebalance_to_symbols(context, ["MSFT"])
            for order in orders:
                getattr(portfolios, order.side)("rotation", order.symbol, order.shares, second)

            state = portfolios.state("rotation", through=second)
            self.assertEqual(state.cash, 0.0)
            self.assertEqual(state.holdings["MSFT"].shares, 20)

    def test_buy_and_hold_persists_run_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_backtest_db(Path(tmp))
            service = BacktestService(db)

            result = service.run(
                "buy-and-hold",
                ["AAPL", "MSFT"],
                date(2024, 1, 2),
                date(2024, 1, 11),
                1000.0,
            )

            self.assertEqual(result.trades_executed, 2)
            self.assertGreater(result.metrics["end_value"], result.metrics["start_value"])
            row = db.get_backtest_run(result.run_id)
            self.assertIsNotNone(row)
            summary = json.loads(row["result_summary_json"])
            self.assertEqual(summary["portfolio_name"], result.portfolio_name)
            self.assertEqual(summary["symbols"], ["AAPL", "MSFT"])
            self.assertTrue(db.delete_backtest_run(result.run_id))
            self.assertIsNone(db.get_backtest_run(result.run_id))
            self.assertFalse(db.delete_backtest_run(result.run_id))

    def test_moving_average_requires_one_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_backtest_db(Path(tmp))
            service = BacktestService(db)

            with self.assertRaisesRegex(ValueError, "exactly one symbol"):
                service.run(
                    "moving-average",
                    ["AAPL", "MSFT"],
                    date(2024, 1, 2),
                    date(2024, 2, 15),
                    1000.0,
                )

    def test_momentum_executes_after_lookback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_backtest_db(Path(tmp))
            service = BacktestService(db)

            result = service.run(
                "momentum",
                ["AAPL", "MSFT"],
                date(2024, 1, 2),
                date(2024, 2, 20),
                1000.0,
                parameters={"lookback_days": 5, "rebalance_days": 5, "top_n": 1},
            )

            self.assertGreater(result.trades_executed, 0)
            self.assertIn("total_return", result.metrics)

    def test_buy_and_hold_enters_on_first_available_trading_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_backtest_db(Path(tmp))
            service = BacktestService(db)

            result = service.run(
                "buy-and-hold",
                ["AAPL"],
                date(2024, 1, 1),
                date(2024, 1, 5),
                1000.0,
            )

            self.assertEqual(result.trades_executed, 1)

    def test_backtest_ignores_symbols_without_data_in_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            db.initialize()
            db.insert_market_bars(
                [
                    _bar("AAPL", date(2024, 1, 2), 100.0),
                    _bar("AAPL", date(2024, 1, 3), 101.0),
                    _bar("SPY", date(2024, 1, 2), 400.0),
                    _bar("SPY", date(2024, 1, 3), 401.0),
                ]
            )
            service = BacktestService(db)

            result = service.run(
                "equal-weight",
                ["AAPL", "MSFT"],
                date(2024, 1, 2),
                date(2024, 1, 3),
                1000.0,
                parameters={"rebalance_days": 1},
            )

            row = db.get_backtest_run(result.run_id)
            summary = json.loads(row["result_summary_json"])
            self.assertEqual(summary["symbols"], ["AAPL"])
            self.assertGreater(result.trades_executed, 0)

    def test_rebalance_waits_until_symbol_has_current_date_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            db.initialize()
            db.add_symbol("IPO")
            db.insert_market_bars(
                [
                    _bar("AAPL", date(2024, 1, 2), 100.0),
                    _bar("AAPL", date(2024, 1, 3), 100.0),
                    _bar("AAPL", date(2024, 1, 4), 100.0),
                    _bar("IPO", date(2024, 1, 4), 50.0),
                    _bar("SPY", date(2024, 1, 2), 400.0),
                    _bar("SPY", date(2024, 1, 3), 401.0),
                    _bar("SPY", date(2024, 1, 4), 402.0),
                ]
            )
            service = BacktestService(db)

            result = service.run(
                "equal-weight",
                ["AAPL", "IPO"],
                date(2024, 1, 2),
                date(2024, 1, 4),
                1000.0,
                parameters={"rebalance_days": 1},
            )

            trades = db.get_trades(db.get_portfolio(result.portfolio_name)["id"])
            ipo_trades = [trade for trade in trades if trade.symbol == "IPO"]
            # The IPO's first close is the final session, so there is no next open to fill.
            self.assertEqual(ipo_trades, [])

    def test_universe_keyword_expands_to_all_tradeable_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_universe_db(Path(tmp))
            service = BacktestService(db)

            result = service.run(
                "equal-weight",
                ["@universe"],
                date(2024, 1, 2),
                date(2024, 1, 11),
                100000.0,
                parameters={"rebalance_days": 5},
            )

            row = db.get_backtest_run(result.run_id)
            summary = json.loads(row["result_summary_json"])
            self.assertEqual(len(summary["symbols"]), len(DEFAULT_SYMBOLS))
            self.assertGreater(result.trades_executed, 0)

    pass

    def test_rsi_strength_executes_and_reports_trade_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_backtest_db(Path(tmp))
            service = BacktestService(db)
            events = []

            result = service.run(
                "rsi-strength",
                ["AAPL", "MSFT"],
                date(2024, 1, 2),
                date(2024, 2, 20),
                1000.0,
                parameters={"lookback_days": 5, "rebalance_days": 5, "top_n": 1},
                progress=events.append,
            )

            trade_events = [event for event in events if event["type"] == "trade"]
            return_events = [event for event in events if event["type"] == "daily_return"]
            self.assertEqual(len(trade_events), result.trades_executed)
            self.assertGreater(len(return_events), 0)
            self.assertAlmostEqual(
                return_events[-1]["total_return"], result.metrics["total_return"]
            )
            self.assertIn("RETURN day", return_events[-1]["message"])
            self.assertEqual(events[0]["type"], "started")
            self.assertEqual(events[-1]["type"], "completed")

    def test_hybrid_strategy_switches_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            db.initialize()
            start = date(2024, 1, 2)
            bars = []
            for offset in range(16):
                trading_date = start + timedelta(days=offset)
                bars.extend(
                    [
                        _bar("AAPL", trading_date, 100.0 + offset * 4),
                        _bar("MSFT", trading_date, 100.0 - offset),
                        _bar("SPY", trading_date, 400.0 + offset),
                    ]
                )
            db.insert_market_bars(bars)
            service = BacktestService(db)
            events = []

            result = service.run(
                "hybrid",
                ["AAPL", "MSFT"],
                start,
                start + timedelta(days=15),
                1000.0,
                parameters={"lookback_days": 1, "rebalance_days": 5, "top_n": 1},
                progress=events.append,
            )

            hybrid_events = [event for event in events if event["type"] == "strategy_switch"]
            self.assertGreater(result.trades_executed, 0)
            self.assertGreaterEqual(len(hybrid_events), 2)
            self.assertIn("HYBRID initial strategy", hybrid_events[0]["message"])
            self.assertTrue(any("HYBRID switch" in event["message"] for event in hybrid_events))

    def test_neural_strategies_are_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_backtest_db(Path(tmp))
            service = BacktestService(db)

            strategies = service.strategies()

            self.assertIn("nn-pattern", strategies)
            self.assertIn("nn-sector-rotation", strategies)
            self.assertIn("nn-risk-adjusted", strategies)
            self.assertIn("agentic-research", strategies)

    pass

    pass

    pass

    pass


if __name__ == "__main__":
    unittest.main()
