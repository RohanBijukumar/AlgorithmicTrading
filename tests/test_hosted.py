import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import jwt
from starlette.testclient import TestClient

from algotrading.hosted import MAX_BODY, create_app
from algotrading.identity import HostedSettings
from algotrading.portfolio import PortfolioService


class HostedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = HostedSettings(
            "https://research.example.com",
            "https://example.cloudflareaccess.com",
            "a" * 64,
            Path(tmp.name),
        )
        self.subjects = {name: str(uuid4()) for name in ("alice", "bob", "outsider")}
        self.verifier = Mock()
        self.verifier.verify.side_effect = self.verify
        self.app = create_app(self.settings, self.verifier)
        self.registry = self.app.state.registry
        self.alice = self.registry.provision(self.subjects["alice"], "Alice")
        self.bob = self.registry.provision(self.subjects["bob"], "Bob")
        self.client = self.enterContext(TestClient(self.app, base_url=self.settings.origin))

    def verify(self, token):
        if token not in self.subjects:
            raise jwt.InvalidTokenError("Invalid")
        return self.subjects[token]

    def headers(self, who="alice", **extra):
        return {
            "Cf-Access-Jwt-Assertion": who,
            "Origin": self.settings.origin,
            "X-Requested-With": "AlgorithmicTrading",
            "Content-Type": "application/json",
            **extra,
        }

    def test_auth_required_on_pages_assets_and_api(self):
        for path in ["/", "/app.js", "/api/health", "/api/portfolios", "/api/session"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                self.assertEqual(
                    self.client.get(path, headers=self.headers("outsider")).status_code, 403
                )
                self.assertEqual(
                    self.client.get(path, headers=self.headers("forged")).status_code, 401
                )

    def test_disabled_user_rejected_and_jobs_cancel(self):
        workspace = self.app.state.runtime.workspace(self.alice)
        job_id = workspace.backtests.create()
        self.assertFalse(workspace.backtests.cancelled(job_id))
        self.registry.disable(self.alice["subject"])
        self.assertEqual(self.client.get("/api/session", headers=self.headers()).status_code, 403)
        self.assertTrue(workspace.backtests.cancelled(job_id))

    def test_private_portfolios_and_guessed_ids(self):
        response = self.client.post(
            "/api/portfolios", headers=self.headers(), json={"name": "private-alice", "cash": 10000}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(self.client.get("/api/portfolios", headers=self.headers()).json()["portfolios"]), 1
        )
        self.assertEqual(
            self.client.get("/api/portfolios", headers=self.headers("bob")).json()["portfolios"], []
        )
        for method, path in [
            ("GET", "/api/portfolios/private-alice"),
            ("DELETE", "/api/portfolios/private-alice"),
            ("GET", "/api/backtests/1/report"),
            ("GET", "/api/backtests/1/export"),
        ]:
            self.assertEqual(
                self.client.request(method, path, headers=self.headers("bob")).status_code, 404
            )
        self.assertEqual(
            PortfolioService(self.registry.database(self.alice)).state("private-alice").cash, 10000
        )

    def test_private_jobs_cannot_be_read_or_cancelled(self):
        workspace = self.app.state.runtime.workspace(self.alice)
        job_id = workspace.backtests.create()
        self.assertEqual(
            self.client.get("/api/backtest-jobs/" + job_id, headers=self.headers()).status_code, 200
        )
        self.assertEqual(
            self.client.get(
                "/api/backtest-jobs/" + job_id, headers=self.headers("bob")
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/api/backtest-jobs/" + job_id + "/cancel", headers=self.headers("bob"), json={}
            ).status_code,
            404,
        )
        self.assertFalse(workspace.backtests.cancelled(job_id))

    def test_csrf_requires_exact_origin_and_ajax_header(self):
        for extra in [
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {"Origin": "http://research.example.com"},
            {"X-Requested-With": ""},
            {"Sec-Fetch-Site": "cross-site"},
        ]:
            response = self.client.post(
                "/api/portfolios", headers=self.headers(**extra), json={"name": "x", "cash": 10000}
            )
            self.assertEqual(response.status_code, 403)
        headers = self.headers()
        del headers["Origin"]
        self.assertEqual(
            self.client.post("/api/portfolios", headers=headers, json={}).status_code, 403
        )

    def test_security_headers_host_and_body_limits(self):
        response = self.client.get("/", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            self.client.get("/", headers=self.headers(Host="evil.example")).status_code, 400
        )
        self.assertEqual(
            self.client.post(
                "/api/portfolios", headers=self.headers(), content=b"x" * (MAX_BODY + 1)
            ).status_code,
            413,
        )
        self.assertEqual(
            self.client.post("/api/portfolios", headers=self.headers(), json=[]).status_code, 422
        )
        self.assertEqual(
            self.client.get("/docs/security.md", headers=self.headers()).status_code, 404
        )

    def test_session_does_not_reveal_storage_paths(self):
        response = self.client.get("/api/health", headers=self.headers())
        self.assertNotIn(str(self.settings.root), response.text)
        self.assertEqual(
            self.client.get("/api/session", headers=self.headers()).json()["label"], "Alice"
        )

    def test_hosted_work_limits_and_online_research_disabled(self):
        response = self.client.post(
            "/api/market/sync",
            headers=self.headers(),
            json={"all": True, "from": "2024-01-01", "to": "2024-12-31"},
        )
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            "/api/backtests",
            headers=self.headers(),
            json={
                "symbols": ["SPY"],
                "from": "2024-01-01",
                "to": "2024-12-31",
                "agent_online_research": 1,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_write_rate_limit_is_per_user(self):
        for _ in range(20):
            response = self.client.post("/api/unknown", headers=self.headers(), json={})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.post("/api/unknown", headers=self.headers(), json={}).status_code, 429
        )
        self.assertEqual(
            self.client.post("/api/unknown", headers=self.headers("bob"), json={}).status_code, 404
        )

    def test_global_workers_reject_work_without_unbounded_queue(self):
        runtime = self.app.state.runtime
        runtime.slots.acquire()
        runtime.slots.acquire()
        self.addCleanup(runtime.slots.release)
        self.addCleanup(runtime.slots.release)
        workspace = runtime.workspace(self.alice)
        job_id = workspace.backtests.create()
        from algotrading.hosted import Rejected

        with self.assertRaises(Rejected):
            runtime.submit(workspace.backtests, lambda *args: None, job_id, ())
        self.assertEqual(workspace.backtests.snapshot(job_id)["status"], "failed")
