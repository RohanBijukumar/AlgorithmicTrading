"""Local operator commands; intentionally not exposed as web endpoints."""

import argparse
import fcntl
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .identity import HostedSettings
from .tenancy import Registry


def seed_market(registry, subject, source):
    user = registry.get(subject)
    if user is None:
        raise ValueError("Unknown or disabled account")
    destination = registry.database(user)
    with (registry.root / ".server.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Stop the hosted server before importing market data") from exc
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
            src.execute("BEGIN")
            with destination.connect() as dst:
                dst.execute("BEGIN IMMEDIATE")
                if (
                    dst.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0]
                    or dst.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0]
                ):
                    raise ValueError("Market seeding requires a new, empty workspace")
                # Deliberate allowlist: never copy portfolios, trades, runs or research decisions.
                for table in ("symbols", "market_bars", "market_cap_snapshot"):
                    columns = [r[1] for r in dst.execute(f"PRAGMA table_info({table})")]
                    names = ",".join('"' + name + '"' for name in columns)
                    rows = src.execute(f"SELECT {names} FROM {table}")
                    placeholders = ",".join("?" for _ in columns)
                    while batch := rows.fetchmany(1000):
                        dst.executemany(
                            f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({placeholders})",
                            batch,
                        )
    return destination.path


def backup(registry, output):
    if output.resolve().is_relative_to(registry.root.resolve()):
        raise ValueError("Backup destination must be outside the hosted data root")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    # Stop new work for a consistent cross-database snapshot, including the registry.
    with (registry.root / ".server.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            output.rmdir()
            raise ValueError("Stop the hosted server before taking a backup") from exc
        paths = [registry.path] + [registry.database(user).path for user in registry.list()]
        for source in paths:
            target = output / source.relative_to(registry.root)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Backup integrity check failed")
            os.chmod(target, 0o600)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="Provision an existing Access subject, not a password")
    add.add_argument("--subject", required=True)
    add.add_argument("--label", required=True)
    commands.add_parser("list", help="List subjects, status and private CLI database paths")
    disable = commands.add_parser(
        "disable", help="Deny new requests and cancel work at its next checkpoint"
    )
    disable.add_argument("--subject", required=True)
    seed = commands.add_parser("seed-market", help="Copy only market tables into a new workspace")
    seed.add_argument("--subject", required=True)
    seed.add_argument("--source", type=Path, required=True)
    snapshot = commands.add_parser(
        "backup", help="Take verified SQLite snapshots with the server stopped"
    )
    snapshot.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    settings = HostedSettings.from_env()
    registry = Registry(settings.root, settings.issuer)
    if args.command == "add":
        result = registry.provision(args.subject, args.label)
    elif args.command == "disable":
        registry.disable(args.subject)
        result = {"disabled": args.subject}
    elif args.command == "seed-market":
        result = {"database": str(seed_market(registry, args.subject, args.source))}
    elif args.command == "backup":
        result = {"backup": str(backup(registry, args.output))}
    else:
        result = [
            {**user, "database": str(registry.database(user).path)} for user in registry.list()
        ]
    print(json.dumps(result, indent=2))
    if args.command != "list":
        # Contains identifiers and action only, never secrets or the label.
        import sys

        print(
            json.dumps(
                {
                    "event": "operator_action",
                    "action": args.command,
                    "subject": getattr(args, "subject", None),
                }
            ),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
