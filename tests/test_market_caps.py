import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from algotrading.backtest import BacktestService
from algotrading.db import Database, utc_now
from algotrading.market_caps import MarketCapService, parse_screener


def payload(rows):
    return {
        "data": {"rows": [{"symbol": s, "name": name, "marketCap": cap} for s, name, cap in rows]}
    }


class MarketCapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "test.sqlite3")
        self.db.initialize()
        self.caps = MarketCapService(self.db)
        self.caps.refresh(
            payload(
                [
                    ("AAPL", "Apple Common Stock", "1,000"),
                    ("MSFT", "Microsoft Common Stock", "2000"),
                    ("GOOGL", "Alphabet Class A Common Stock", "3000"),
                    ("GOOG", "Alphabet Class C Common Stock", "3000"),
                ]
            )
        )

    def test_arbitrary_top_count_sorted_by_numeric_cap(self):
        service = BacktestService(self.db)
        self.assertEqual(service._resolve_symbols(["@top1"]), ["GOOGL"])
        self.assertEqual(service._resolve_symbols(["@TOP2"]), ["GOOGL", "MSFT"])
        self.assertEqual(service._resolve_symbols(["@top3"]), ["GOOGL", "MSFT", "AAPL"])
        for selector in ("@top0", "@top-2", "@top1.5", "@top4"):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                service._resolve_symbols([selector])
        with self.assertRaises(ValueError):
            service._resolve_symbols(["@top2", "AAPL"])

    def test_deactivation_and_missing_caps_are_excluded(self):
        self.db.deactivate_symbol("MSFT")
        self.assertEqual([r["symbol"] for r in self.caps.ranked(2)], ["GOOGL", "AAPL"])

    def test_fresh_cache_avoids_network_and_stale_cache_fails_closed(self):
        with patch.object(self.caps, "refresh", side_effect=OSError("offline")) as refresh:
            self.caps.ranked(1)
            refresh.assert_not_called()
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE market_cap_snapshot SET fetched_at=?",
                    ((utc_now() - timedelta(days=2)).isoformat(),),
                )
            with self.assertRaisesRegex(ValueError, "Current market caps unavailable"):
                self.caps.ranked(1)

    def test_bad_response_does_not_destroy_good_snapshot(self):
        with self.assertRaises(ValueError):
            self.caps.refresh({"data": {"rows": []}})
        self.assertEqual(self.caps.status()["companies"], 4)

    def test_expansion_retains_user_deactivations(self):
        self.db.deactivate_symbol("MSFT")
        self.caps.refresh(
            payload(
                [
                    ("ZZTEST", "New Company Common Stock", "100000"),
                    ("MSFT", "Microsoft Common Stock", "2000"),
                ]
            )
        )
        result = self.caps.expand(2)
        self.assertEqual(result["added"], 1)
        self.assertTrue(self.db.get_symbol("ZZTEST")["active"])
        self.assertIsNone(self.db.get_symbol("MSFT"))

    def test_parser_excludes_funds_non_equities_and_invalid_caps(self):
        rows = parse_screener(
            payload(
                [
                    ("AAA", "Alpha Common Stock", "2,000"),
                    ("BBB", "Beta ETF", "9000"),
                    ("CCC", "Charlie Warrants", "8000"),
                    ("DDD", "Delta Common Stock", "NaN"),
                    ("EEE", "Echo Common Stock", "0"),
                    ("BK", "Removed Common Stock", "100000"),
                    ("BRK/B", "Berkshire Class B Common Stock", "5000"),
                ]
            )
        )
        self.assertEqual([r["symbol"] for r in rows], ["BRK.B", "AAA"])
