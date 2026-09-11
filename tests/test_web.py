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

    def test_job_keeps_fractional_costs_and_live_metrics_out_of_activity(self):
        job_id = self.handler.backtest_jobs.create()
        self.handler._run_backtest_job(
            job_id,
            {
                "strategy": "buy-and-hold",
                "symbols": ["AAPL"],
                "from": "2024-01-02",
                "to": "2024-01-12",
                "cash": 10000,
            },
            {"commission": 0.25, "slippage_bps": 2.5},
        )
        job = self.handler.backtest_jobs.snapshot(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["metrics"]["fees"], 0.25)
        self.assertTrue(job["latest"])
        self.assertFalse(any(e["type"] == "daily_return" for e in job["events"]))
        self.handler._handle_get(f"/api/backtests/{job['result']['run_id']}/report", {})
        json.dumps(self.responses[-1], default=str, allow_nan=False)

    def test_preflight_does_not_create_runs(self):
        self.handler._handle_post(
            "/api/backtests/preflight",
            {
                "strategy": "rsi-strength",
                "symbols": ["AAPL", "NVDA"],
                "from": "2024-01-02",
                "to": "2024-02-15",
                "lookback_days": 14,
            },
        )
        result = self.responses[-1]
        self.assertEqual(result["missing_symbols"], ["NVDA"])
        self.assertEqual(result["warmup_missing"], ["AAPL"])
        self.assertEqual(self.db.list_backtest_runs(), [])

    def test_cross_origin_mutation_rejected(self):
        self.handler.headers = {"Origin": "https://unrelated.example", "Host": "127.0.0.1:8000"}
        with self.assertRaisesRegex(ValueError, "cross-origin"):
            self.handler._check_origin()
        self.handler.headers = {"Origin": "http://127.0.0.1:8000", "Host": "127.0.0.1:8000"}
        self.handler._check_origin()

    def test_trade_ticket_never_truncates_fractional_shares(self):
        for value in [1.2, "1.2", True, -1, "NaN", "Infinity"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                _shares(value)
