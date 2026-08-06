from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from algotrading.db import Database, utc_now
from algotrading.market import MarketService
from algotrading.models import FetchResult, MarketBar


class FakeProvider:
    name = "yahoo"

    def __init__(self):
        self.calls: list[tuple[str, date, date]] = []

    def fetch_daily(
        self, symbol: str, start: date, end: date, provider_symbol: str | None = None
    ) -> FetchResult:
        self.calls.append((symbol, start, end))
        bars = [
            MarketBar(
                symbol=symbol,
                trading_date=date(2024, 1, 2),
                open=100.0,
                high=110.0,
                low=99.0,
                close=105.0,
                adjusted_close=None,
                volume=1000,
                provider=self.name,
            ),
            MarketBar(
                symbol=symbol,
                trading_date=date(2024, 1, 3),
                open=105.0,
                high=112.0,
                low=104.0,
                close=111.0,
                adjusted_close=None,
                volume=1200,
                provider=self.name,
            ),
        ]
        return FetchResult(
            provider=self.name,
            symbol=symbol,
            provider_symbol=f"{symbol.lower()}.us",
            requested_start=start,
            requested_end=end,
            bars=bars,
            fetched_at=utc_now(),
            success=True,
        )


class MarketServiceTests(unittest.TestCase):
    pass

    pass

    def test_sync_caches_fully_covered_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            provider = FakeProvider()
            service = MarketService(db, provider)

            first = service.sync(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))
            second = service.sync(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

            self.assertEqual(first, {"AAPL": 2})
            self.assertEqual(second, {"AAPL": 0})
            self.assertEqual(len(provider.calls), 1)

    pass

    def test_price_uses_previous_trading_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            provider = FakeProvider()
            service = MarketService(db, provider)
            service.sync(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

            bar = service.price("AAPL", date(2024, 1, 4))

            self.assertEqual(bar.trading_date, date(2024, 1, 3))
            self.assertEqual(bar.close, 111.0)

    def test_replay_groups_bars_by_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite3")
            provider = FakeProvider()
            service = MarketService(db, provider)
            service.sync(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

            replay = service.replay(date(2024, 1, 2), date(2024, 1, 3))

            self.assertEqual([item[0] for item in replay], [date(2024, 1, 2), date(2024, 1, 3)])
            self.assertEqual([len(item[1]) for item in replay], [1, 1])

    pass

    pass


if __name__ == "__main__":
    unittest.main()
