from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from algotrading.db import Database
from algotrading.models import MarketBar
from algotrading.portfolio import PortfolioService


def seeded_db(path: Path):
    db = Database(path / "test.sqlite3")
    db.initialize()
    db.insert_market_bars(
        [
            MarketBar(
                symbol="AAPL",
                trading_date=date(2024, 1, 2),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                adjusted_close=None,
                volume=1000,
                provider="yahoo",
            ),
            MarketBar(
                symbol="AAPL",
                trading_date=date(2024, 1, 3),
                open=110.0,
                high=112.0,
                low=109.0,
                close=110.0,
                adjusted_close=None,
                volume=1000,
                provider="yahoo",
            ),
            MarketBar(
                symbol="AAPL",
                trading_date=date(2024, 1, 4),
                open=90.0,
                high=92.0,
                low=89.0,
                close=90.0,
                adjusted_close=None,
                volume=1000,
                provider="yahoo",
            ),
        ]
    )
    return db


class PortfolioServiceTests(unittest.TestCase):
    def test_buy_sell_reconstructs_state_and_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_db(Path(tmp))
            service = PortfolioService(db)
            service.create("demo", 1000.0)

            service.buy("demo", "AAPL", 5, date(2024, 1, 2))
            service.sell("demo", "AAPL", 2, date(2024, 1, 3))

            state = service.state("demo")
            valuation = service.value("demo", date(2024, 1, 4))

            self.assertEqual(state.cash, 720.0)
            self.assertEqual(state.holdings["AAPL"].shares, 3)
            self.assertEqual(state.holdings["AAPL"].average_cost, 100.0)
            self.assertEqual(state.realized_pnl, 20.0)
            self.assertEqual(valuation.market_value, 270.0)
            self.assertEqual(valuation.total_value, 990.0)
            self.assertEqual(valuation.unrealized_pnl, -30.0)

    def test_value_history_returns_total_value_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_db(Path(tmp))
            service = PortfolioService(db)
            service.create("demo", 1000.0)

            service.buy("demo", "AAPL", 5, date(2024, 1, 2))
            points = service.value_history("demo", end=date(2024, 1, 4))

            self.assertEqual(
                [point.valuation_date for point in points],
                [
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                ],
            )
            self.assertEqual([point.total_value for point in points], [1000.0, 1050.0, 950.0])
            self.assertEqual(points[-1].cash, 500.0)

    def test_buy_rejects_insufficient_cash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_db(Path(tmp))
            service = PortfolioService(db)
            service.create("demo", 50.0)

            with self.assertRaisesRegex(ValueError, "insufficient cash"):
                service.buy("demo", "AAPL", 1, date(2024, 1, 2))

    def test_sell_rejects_insufficient_shares(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_db(Path(tmp))
            service = PortfolioService(db)
            service.create("demo", 1000.0)

            with self.assertRaisesRegex(ValueError, "insufficient shares"):
                service.sell("demo", "AAPL", 1, date(2024, 1, 2))

    def test_delete_portfolio_removes_trade_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = seeded_db(Path(tmp))
            service = PortfolioService(db)
            service.create("demo", 1000.0)
            service.buy("demo", "AAPL", 1, date(2024, 1, 2))

            self.assertTrue(db.delete_portfolio("demo"))
            self.assertIsNone(db.get_portfolio("demo"))
            self.assertFalse(db.delete_portfolio("demo"))


if __name__ == "__main__":
    unittest.main()
