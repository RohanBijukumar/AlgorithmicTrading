from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_backtest import seeded_backtest_db

from algotrading.jobs import JobStore
from algotrading.web import AppHandler, _shares


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = seeded_backtest_db(Path(self.tmp.name))
        self.handler = object.__new__(AppHandler)
        self.handler.database = self.db
        self.handler.backtest_jobs = JobStore()
        self.handler.market_sync_jobs = JobStore()
        self.responses = []
        self.handler._json = lambda payload, *args: self.responses.append(payload)

    def test_overview_coverage_and_catalog(self):
        self.handler._handle_get("/api/overview", {})
        self.assertEqual(self.responses[-1]["covered_symbols"], 4)
        self.handler._handle_get("/api/backtests/strategies", {})
        self.assertEqual(len(self.responses[-1]["catalog"]), 17)
        self.handler._handle_get("/api/market/coverage", {})
        self.assertTrue(
            any(
                s["symbol"] == "AAPL" and s["bar_count"] == 70
                for s in self.responses[-1]["symbols"]
            )
        )

    pass

    pass

    def test_cross_origin_mutation_rejected(self):
        self.handler.headers = {"Origin": "https://unrelated.example", "Host": "127.0.0.1:8000"}
        with self.assertRaisesRegex(ValueError, "cross-origin"):
            self.handler._check_origin()
        self.handler.headers = {"Origin": "http://127.0.0.1:8000", "Host": "127.0.0.1:8000"}
        self.handler._check_origin()

    pass
