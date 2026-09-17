import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError as exc:
    raise unittest.SkipTest("Install .[hosted,hosted-test] for identity tests") from exc

from algotrading.identity import AccessVerifier, HostedSettings
from algotrading.tenancy import Registry


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.settings = HostedSettings(
            "https://research.example.com",
            "https://example.cloudflareaccess.com",
            "a" * 64,
            Path("/tmp/hosted-test"),
        )
        self.verifier = AccessVerifier(self.settings)
        self.verifier.client = Mock()
        self.verifier.client.get_signing_keys.return_value = [
            SimpleNamespace(key_id="test", key=self.key.public_key())
        ]
        self.subject = str(uuid4())

    def token(self, **changes):
        now = int(time.time())
        claims = dict(
            iss=self.settings.issuer,
            aud=[self.settings.audience],
            sub=self.subject,
            iat=now,
            exp=now + 1800,
            type="app",
        )
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test"})

    def test_valid_subject_uses_verified_key(self):
        self.assertEqual(self.verifier.verify(self.token()), self.subject)
        self.assertEqual(self.verifier.verify(self.token()), self.subject)
        self.verifier.client.get_signing_keys.assert_called_once()

    def test_rejects_wrong_issuer_audience_expiry_and_subject(self):
        for changes in [
            dict(iss="https://evil.example"),
            dict(aud="wrong"),
            dict(exp=1),
            dict(iat=int(time.time()) + 300),
            dict(sub=""),
            dict(type="service"),
            dict(exp=int(time.time()) + 7200),
        ]:
            with self.subTest(changes=changes), self.assertRaises(jwt.InvalidTokenError):
                self.verifier.verify(self.token(**changes))

    def test_rejects_unsigned_tampered_and_unknown_key_tokens(self):
        for token in [
            "",
            jwt.encode({"sub": self.subject}, "", algorithm="none"),
            jwt.encode({"sub": self.subject}, "x" * 32, algorithm="HS256"),
            self.token()[:-10] + "AAAAAAAAAA",
        ]:
            with self.assertRaises(jwt.InvalidTokenError):
                self.verifier.verify(token)
        self.verifier.verify(self.token())
        for _ in range(10):
            token = jwt.encode(
                {"sub": self.subject}, self.key, algorithm="RS256", headers={"kid": str(uuid4())}
            )
            with self.assertRaises(jwt.InvalidTokenError):
                self.verifier.verify(token)
        self.verifier.client.get_signing_keys.assert_called_once()

    def test_settings_fail_closed(self):
        for origin in [
            "http://research.example.com",
            "https://a.example/",
            "https://name:password@a.example",
            "https://a.example?x=1",
        ]:
            with self.assertRaises(ValueError):
                HostedSettings(origin, self.settings.issuer, self.settings.audience, Path("/tmp/x"))

    def test_missing_claims_and_signing_service_outage_fail_closed(self):
        token = jwt.encode(
            {"sub": self.subject}, self.key, algorithm="RS256", headers={"kid": "test"}
        )
        with self.assertRaises(jwt.InvalidTokenError):
            self.verifier.verify(token)
        self.verifier.refreshed -= 301
        self.verifier.client.get_signing_keys.side_effect = jwt.PyJWKClientConnectionError(
            "offline"
        )
        with self.assertRaises(jwt.PyJWKClientConnectionError):
            self.verifier.verify(self.token())
        with self.assertRaises(jwt.InvalidTokenError):
            self.verifier.verify(self.token())

    def test_rotation_drops_removed_signing_keys(self):
        self.verifier.verify(self.token())
        self.verifier.refreshed -= 301
        self.verifier.client.get_signing_keys.return_value = [
            SimpleNamespace(key_id="rotated", key=self.key.public_key())
        ]
        with self.assertRaises(jwt.InvalidTokenError):
            self.verifier.verify(self.token())

    def test_registry_isolated_and_disable_is_immediate(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Registry(Path(tmp), self.settings.issuer)
            alice = registry.provision(self.subject, "Alice")
            bob = registry.provision(str(uuid4()), "Bob")
            self.assertNotEqual(registry.database(alice).path, registry.database(bob).path)
            self.assertEqual(registry.database(alice).list_portfolios(), [])
            self.assertEqual(registry.database(bob).list_backtest_runs(), [])
            with self.assertRaises(ValueError):
                registry.provision(self.subject, "Duplicate")
            registry.disable(self.subject)
            self.assertIsNone(registry.get(self.subject))
            self.assertIsNotNone(registry.get(bob["subject"]))
            with self.assertRaises(ValueError):
                Registry(Path(tmp), "https://other.cloudflareaccess.com")
