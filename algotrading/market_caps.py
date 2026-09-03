"""Bulk, timestamped Nasdaq market-cap snapshots; no hand-ranked fallback."""

from __future__ import annotations

import json
import math
import re
import ssl
import urllib.request
from datetime import datetime, timedelta

from .db import to_yahoo_symbol, utc_now

URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true"
EXCLUDED = {"BK", "PARA"}  # Previously removed at the user's request.
ALTERNATE_CLASSES = {"GOOG": "GOOGL", "BRK.A": "BRK.B", "FOX": "FOXA", "NWS": "NWSA"}


def parse_screener(payload):
    data = payload.get("data") or {}
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Nasdaq returned no screener rows")
    stocks = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).strip().upper().replace("/", ".")
        name = str(row.get("name", "")).strip()
        if not re.fullmatch(r"[A-Z]{1,6}(?:\.[A-Z])?", symbol) or symbol in EXCLUDED:
            continue
        if re.search(
            r"\b(warrants?|rights?|units?|preferred|debentures?|notes?|ETF|ETN|fund)\b", name, re.I
        ):
            continue
        try:
            cap = float(str(row.get("marketCap", "")).replace(",", "").replace("$", ""))
        except ValueError:
            continue
        if not math.isfinite(cap) or cap <= 0:
            continue
        stocks[symbol] = {
            "symbol": symbol,
            "name": name,
            "market_cap": cap,
            "sector": str(row.get("sector", "")),
            "currency": "USD",
        }
    if not stocks:
        raise ValueError("Nasdaq returned no usable positive market caps")
    return sorted(stocks.values(), key=lambda row: (-row["market_cap"], row["symbol"]))


class MarketCapService:
    def __init__(self, db):
        self.db = db

    def refresh(self, payload=None):
        if payload is None:
            request = urllib.request.Request(
                URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            )
            context = ssl.create_default_context()
            try:
                import certifi

                context.load_verify_locations(certifi.where())
            except ImportError:
                pass
            with urllib.request.urlopen(request, timeout=20, context=context) as response:
                payload = json.loads(response.read(8_000_001))
        stocks = parse_screener(payload)
        self.db.initialize()
        fetched = utc_now().isoformat()
        with self.db.transaction():
            with self.db.connect() as conn:
                conn.execute("DELETE FROM market_cap_snapshot")
                conn.executemany(
                    "INSERT INTO market_cap_snapshot(symbol, asset_name, market_cap, sector, fetched_at, provider_as_of) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            r["symbol"],
                            r["name"],
                            r["market_cap"],
                            r["sector"],
                            fetched,
                            str(payload["data"].get("asOf") or "unknown"),
                        )
                        for r in stocks
                    ],
                )
        return self.status()

    def status(self):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS companies, MAX(fetched_at) AS fetched_at, MAX(provider_as_of) AS provider_as_of FROM market_cap_snapshot"
            ).fetchone()
        return {
            **dict(row),
            "provider": "Nasdaq screener",
            "currency": "USD",
            "scope": "US-listed common shares and ADRs; not worldwide exchange coverage",
        }

    def ensure_current(self):
        status = self.status()
        if not status["fetched_at"] or utc_now() - datetime.fromisoformat(
            status["fetched_at"]
        ) > timedelta(hours=24):
            try:
                self.refresh()
            except Exception as error:
                raise ValueError(
                    "Current market caps unavailable. Run 'market cap-refresh' with internet access; stale/static rankings are not used."
                ) from error

    def ranked(self, count, active_only=True):
        if type(count) is not int or count <= 0:
            raise ValueError("@topN requires a positive integer")
        self.ensure_current()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT c.* FROM market_cap_snapshot c "
                + (
                    "JOIN symbols s ON s.symbol=c.symbol WHERE s.active=1 AND s.asset_type='equity' "
                    if active_only
                    else ""
                )
                + "ORDER BY c.market_cap DESC, c.symbol"
            ).fetchall()
        eligible = {r["symbol"] for r in rows}
        rows = [dict(r) for r in rows if ALTERNATE_CLASSES.get(r["symbol"]) not in eligible]
        if active_only and count > len(rows):
            raise ValueError(
                f"@top{count} requested, but only {len(rows)} active companies have current caps. Use 'market expand --count {count}' first."
            )
        return rows[:count]

    def expand(self, count=1500):
        rows = self.ranked(count, active_only=False)
        added = 0
        with self.db.transaction():
            with self.db.connect() as conn:
                for row in rows:
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO symbols(symbol,provider_symbol,asset_name,asset_type,active,sector) VALUES (?,?,?,'equity',1,?)",
                        (
                            row["symbol"],
                            to_yahoo_symbol(row["symbol"]),
                            row["asset_name"],
                            row["sector"],
                        ),
                    )
                    added += cursor.rowcount
                    conn.execute(
                        "UPDATE symbols SET sector=? WHERE symbol=? AND (sector IS NULL OR sector='')",
                        (row["sector"], row["symbol"]),
                    )
        return {
            "added": added,
            "active": len(self.db.list_symbols()),
            "requested": count,
            "ranked_available": len(rows),
            **self.status(),
        }
