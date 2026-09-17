import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from uuid import uuid4

from test_backtest import seeded_backtest_db

if find_spec("jwt") is None:
    raise unittest.SkipTest("Install .[hosted,hosted-test] for hosted operator tests")

from algotrading.hosted_admin import backup, seed_market
from algotrading.portfolio import PortfolioService
from algotrading.tenancy import Registry


class HostedAdminTests(unittest.TestCase):
    def test_seed_copies_prices_but_never_personal_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = seeded_backtest_db(root)
            PortfolioService(source).create("owner-secret", 10000)
            registry = Registry(root / "hosted", "https://example.cloudflareaccess.com")
            user = registry.provision(str(uuid4()), "Alice")
            seed_market(registry, user["subject"], source.path)
            database = registry.database(user)
            self.assertEqual(database.list_portfolios(), [])
            self.assertEqual(database.list_backtest_runs(), [])
            self.assertEqual(sum(row["bar_count"] for row in database.coverage_report()), 280)
            with self.assertRaises(ValueError):
                seed_market(registry, user["subject"], source.path)
            self.assertEqual(len(source.list_portfolios()), 1)

    def test_verified_backup_includes_disabled_users_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = Registry(root / "hosted", "https://example.cloudflareaccess.com")
            user = registry.provision(str(uuid4()), "Alice")
            PortfolioService(registry.database(user)).create("keep", 10000)
            registry.disable(user["subject"])
            output = backup(registry, root / "snapshot")
            restored = Registry(output, "https://example.cloudflareaccess.com")
            self.assertIsNone(restored.get(user["subject"]))
            self.assertEqual(restored.database(user).get_portfolio("keep")["starting_cash"], 10000)
            with self.assertRaises(FileExistsError):
                backup(registry, output)
