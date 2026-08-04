from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime
from math import isfinite
from pathlib import Path
from typing import Iterator

from .config import DAILY_INTERVAL, DEFAULT_PROVIDER
from .models import FetchResult, MarketBar, Trade
from .universe import DEFAULT_SYMBOLS, ETF_SYMBOLS, normalize_symbol


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def utc_now() -> datetime:
    return datetime.now(tz=UTC).replace(microsecond=0)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._local = threading.local()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            yield existing
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """Serialize ledger validation and writes across CLI and web connections."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._local.connection = conn
            try:
                yield conn
            finally:
                self._local.connection = None

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol TEXT PRIMARY KEY,
                    provider_symbol TEXT NOT NULL,
                    asset_name TEXT,
                    asset_type TEXT NOT NULL DEFAULT 'equity',
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS market_bars (
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    adjusted_close REAL,
                    volume INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    price_basis TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, trading_date, interval, provider),
                    FOREIGN KEY (symbol) REFERENCES symbols(symbol)
                );

                CREATE TABLE IF NOT EXISTS market_cap_snapshot (
                    symbol TEXT PRIMARY KEY,
                    asset_name TEXT NOT NULL,
                    market_cap REAL NOT NULL,
                    sector TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    provider_as_of TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_market_bars_symbol_date
                    ON market_bars(symbol, trading_date);

                CREATE TABLE IF NOT EXISTS provider_fetches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    requested_start TEXT NOT NULL,
                    requested_end TEXT NOT NULL,
                    fetched_row_count INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS portfolios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    starting_cash REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    portfolio_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                    shares INTEGER NOT NULL CHECK (shares > 0),
                    execution_date TEXT NOT NULL,
                    execution_price REAL NOT NULL,
                    cash_impact REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
                    FOREIGN KEY (symbol) REFERENCES symbols(symbol)
                );

                CREATE INDEX IF NOT EXISTS idx_trades_portfolio_date
                    ON trades(portfolio_id, execution_date, id);

                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    portfolio_id INTEGER NOT NULL,
                    valuation_date TEXT NOT NULL,
                    cash REAL NOT NULL,
                    market_value REAL NOT NULL,
                    total_value REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (portfolio_id, valuation_date),
                    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
                );

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    result_summary_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    portfolio_id INTEGER NOT NULL,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    research_date TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    score REAL NOT NULL,
                    rationale TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
                    FOREIGN KEY (symbol) REFERENCES symbols(symbol)
                );

                CREATE INDEX IF NOT EXISTS idx_research_decisions_portfolio_date
                    ON research_decisions(portfolio_id, research_date, id);
                """
            )
            for table, columns in {
                "symbols": {"sector": "TEXT"},
                "trades": {
                    "fees": "REAL NOT NULL DEFAULT 0",
                    "slippage": "REAL NOT NULL DEFAULT 0",
                },
                "portfolio_snapshots": {
                    "realized_pnl": "REAL NOT NULL DEFAULT 0",
                    "unrealized_pnl": "REAL NOT NULL DEFAULT 0",
                    "holding_values_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            }.items():
                present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, declaration in columns.items():
                    if name not in present:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            conn.executemany(
                """
                INSERT OR IGNORE INTO symbols(symbol, provider_symbol, asset_type, active)
                VALUES (?, ?, ?, 1)
                """,
                [
                    (symbol, to_yahoo_symbol(symbol), "etf" if symbol in ETF_SYMBOLS else "equity")
                    for symbol in DEFAULT_SYMBOLS
                ],
            )
            conn.executemany(
                "UPDATE symbols SET asset_type='etf' WHERE symbol=?", [(s,) for s in ETF_SYMBOLS]
            )

    def add_symbol(
        self,
        symbol: str,
        provider_symbol: str | None = None,
        asset_name: str | None = None,
        asset_type: str = "equity",
        active: bool = True,
    ) -> str:
        normalized = normalize_symbol(symbol)
        provider = provider_symbol.strip() if provider_symbol else to_yahoo_symbol(normalized)
        if not provider:
            raise ValueError("provider symbol cannot be empty")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO symbols(symbol, provider_symbol, asset_name, asset_type, active)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    provider_symbol = excluded.provider_symbol,
                    asset_name = excluded.asset_name,
                    asset_type = excluded.asset_type,
                    active = excluded.active
                """,
                (normalized, provider, asset_name, asset_type, int(active)),
            )
        return normalized

    def list_symbols(self, active_only: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM symbols"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY symbol"
        with self.connect() as conn:
            return conn.execute(query).fetchall()

    def get_symbol(self, symbol: str) -> sqlite3.Row | None:
        normalized = normalize_symbol(symbol)
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM symbols WHERE symbol = ? AND active = 1",
                (normalized,),
            ).fetchone()

    def require_symbol(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        if self.get_symbol(normalized) is None:
            raise ValueError(
                f"unsupported symbol: {normalized}. Add it with `market add-symbol {normalized}`."
            )
        return normalized

    def deactivate_symbol(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE symbols SET active = 0 WHERE symbol = ?",
                (normalized,),
            )
        if cursor.rowcount == 0:
            raise ValueError(f"symbol not found: {normalized}")
        return normalized

    def insert_market_bars(self, bars: list[MarketBar], fetched_at: datetime | None = None) -> int:
        if not bars:
            return 0
        for bar in bars:
            prices = [bar.open, bar.high, bar.low, bar.close]
            if bar.adjusted_close is not None:
                prices.append(bar.adjusted_close)
            if any(not isfinite(p) or p <= 0 for p in prices) or bar.volume < 0:
                raise ValueError(f"invalid price or volume for {bar.symbol} on {bar.trading_date}")
            if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close):
                raise ValueError(f"inconsistent OHLC for {bar.symbol} on {bar.trading_date}")
        timestamp = (fetched_at or utc_now()).isoformat()
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO market_bars (
                    symbol, trading_date, interval, open, high, low, close,
                    adjusted_close, volume, provider, price_basis, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        bar.symbol,
                        bar.trading_date.isoformat(),
                        bar.interval,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.adjusted_close,
                        bar.volume,
                        bar.provider,
                        bar.price_basis,
                        timestamp,
                    )
                    for bar in bars
                ],
            )
        return len(bars)

    def record_fetch(self, result: FetchResult) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_fetches (
                    provider, symbol, provider_symbol, requested_start, requested_end,
                    fetched_row_count, fetched_at, success, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.provider,
                    result.symbol,
                    result.provider_symbol,
                    result.requested_start.isoformat(),
                    result.requested_end.isoformat(),
                    len(result.bars),
                    result.fetched_at.isoformat(),
                    int(result.success),
                    result.error_message,
                ),
            )

    def get_coverage(
        self,
        symbol: str,
        provider: str = DEFAULT_PROVIDER,
        interval: str = DAILY_INTERVAL,
    ) -> tuple[date, date] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(trading_date) AS min_date, MAX(trading_date) AS max_date
                FROM market_bars
                WHERE symbol = ? AND provider = ? AND interval = ?
                """,
                (symbol, provider, interval),
            ).fetchone()
        if not row or row["min_date"] is None:
            return None
        return parse_date(row["min_date"]), parse_date(row["max_date"])

    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        provider: str = DEFAULT_PROVIDER,
        interval: str = DAILY_INTERVAL,
    ) -> list[MarketBar]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM market_bars
                WHERE symbol = ? AND provider = ? AND interval = ?
                  AND trading_date BETWEEN ? AND ?
                ORDER BY trading_date
                """,
                (symbol, provider, interval, start.isoformat(), end.isoformat()),
            ).fetchall()
        return [row_to_bar(row) for row in rows]

    def get_bar_on_or_before(
        self,
        symbol: str,
        target_date: date,
        provider: str = DEFAULT_PROVIDER,
        interval: str = DAILY_INTERVAL,
    ) -> MarketBar | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM market_bars
                WHERE symbol = ? AND provider = ? AND interval = ?
                  AND trading_date <= ?
                ORDER BY trading_date DESC
                LIMIT 1
                """,
                (symbol, provider, interval, target_date.isoformat()),
            ).fetchone()
        return row_to_bar(row) if row else None

    def latest_trading_date(self) -> date | None:
        with self.connect() as conn:
            row = conn.execute("SELECT MAX(trading_date) AS max_date FROM market_bars").fetchone()
        return parse_date(row["max_date"]) if row and row["max_date"] else None

    def trading_dates(
        self,
        start: date,
        end: date,
        symbols: list[str] | None = None,
        provider: str = DEFAULT_PROVIDER,
        interval: str = DAILY_INTERVAL,
    ) -> list[date]:
        query = """
            SELECT DISTINCT trading_date
            FROM market_bars
            WHERE provider = ? AND interval = ?
              AND trading_date BETWEEN ? AND ?
        """
        params: list[object] = [provider, interval, start.isoformat(), end.isoformat()]
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            query += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        query += " ORDER BY trading_date"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [parse_date(row["trading_date"]) for row in rows]

    def create_portfolio(self, name: str, starting_cash: float) -> int:
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError("portfolio name must contain 1 to 120 characters")
        if not isfinite(starting_cash) or starting_cash < 0:
            raise ValueError("starting cash must be finite and nonnegative")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO portfolios(name, starting_cash, created_at)
                VALUES (?, ?, ?)
                """,
                (name, starting_cash, utc_now().isoformat()),
            )
            return int(cursor.lastrowid)

    def get_portfolio(self, name: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM portfolios WHERE name = ?", (name,)).fetchone()

    def delete_portfolio(self, name: str) -> bool:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM portfolios WHERE name = ?", (name,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM research_decisions WHERE portfolio_id = ?", (row["id"],))
            conn.execute("DELETE FROM trades WHERE portfolio_id = ?", (row["id"],))
            conn.execute("DELETE FROM portfolio_snapshots WHERE portfolio_id = ?", (row["id"],))
            conn.execute(
                "DELETE FROM backtest_runs WHERE json_extract(result_summary_json, '$.portfolio_name') = ?",
                (name,),
            )
            conn.execute("DELETE FROM portfolios WHERE id = ?", (row["id"],))
            return True

    def list_portfolios(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM portfolios
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()

    def insert_trade(
        self,
        portfolio_id: int,
        symbol: str,
        side: str,
        shares: int,
        execution_date: date,
        execution_price: float,
        cash_impact: float,
        fees: float = 0.0,
        slippage: float = 0.0,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (
                    portfolio_id, symbol, side, shares, execution_date,
                    execution_price, cash_impact, created_at, fees, slippage
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    symbol,
                    side,
                    shares,
                    execution_date.isoformat(),
                    execution_price,
                    cash_impact,
                    utc_now().isoformat(),
                    fees,
                    slippage,
                ),
            )
            return int(cursor.lastrowid)

    def get_trades(self, portfolio_id: int, through: date | None = None) -> list[Trade]:
        query = "SELECT * FROM trades WHERE portfolio_id = ?"
        params: list[object] = [portfolio_id]
        if through is not None:
            query += " AND execution_date <= ?"
            params.append(through.isoformat())
        query += " ORDER BY execution_date, id"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            Trade(
                id=row["id"],
                portfolio_id=row["portfolio_id"],
                symbol=row["symbol"],
                side=row["side"],
                shares=row["shares"],
                execution_date=parse_date(row["execution_date"]),
                execution_price=row["execution_price"],
                cash_impact=row["cash_impact"],
                created_at=datetime.fromisoformat(row["created_at"]),
                fees=row["fees"],
                slippage=row["slippage"],
            )
            for row in rows
        ]

    def save_snapshots(self, portfolio_id: int, points: list[dict]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO portfolio_snapshots
                (portfolio_id, valuation_date, cash, market_value, total_value, created_at, realized_pnl, unrealized_pnl, holding_values_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        portfolio_id,
                        str(p["date"]),
                        p["cash"],
                        p["market_value"],
                        p["total_value"],
                        utc_now().isoformat(),
                        p["realized_pnl"],
                        p["unrealized_pnl"],
                        json.dumps(p.get("holding_values", {})),
                    )
                    for p in points
                ],
            )

    def get_snapshots(self, portfolio_id: int) -> list[dict]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM portfolio_snapshots WHERE portfolio_id = ? ORDER BY valuation_date",
                    (portfolio_id,),
                )
            ]

    def coverage_report(self) -> list[dict]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT s.*, MIN(b.trading_date) AS first_date, MAX(b.trading_date) AS last_date,
                COUNT(b.trading_date) AS bar_count, MAX(b.fetched_at) AS fetched_at,
                c.market_cap, c.fetched_at AS cap_fetched_at
                FROM symbols s LEFT JOIN market_bars b ON s.symbol=b.symbol AND b.provider=? AND b.interval=?
                LEFT JOIN market_cap_snapshot c ON c.symbol=s.symbol
                WHERE s.active=1 GROUP BY s.symbol ORDER BY s.symbol""",
                    (DEFAULT_PROVIDER, DAILY_INTERVAL),
                )
            ]

    def insert_backtest_run(
        self,
        strategy_name: str,
        start_date: date,
        end_date: date,
        parameters: dict,
        result_summary: dict,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO backtest_runs (
                    strategy_name, start_date, end_date, parameters_json,
                    result_summary_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_name,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    json.dumps(parameters, sort_keys=True),
                    json.dumps(result_summary, sort_keys=True),
                    utc_now().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def list_backtest_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM backtest_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def get_backtest_run(self, run_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM backtest_runs WHERE id = ?",
                (run_id,),
            ).fetchone()

    def delete_backtest_run(self, run_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
            return cursor.rowcount > 0

    def insert_research_decision(
        self,
        portfolio_id: int,
        strategy_name: str,
        symbol: str,
        research_date: date,
        verdict: str,
        score: float,
        rationale: str,
        source: str,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO research_decisions (
                    portfolio_id, strategy_name, symbol, research_date, verdict,
                    score, rationale, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    strategy_name,
                    symbol,
                    research_date.isoformat(),
                    verdict,
                    score,
                    rationale,
                    source,
                    utc_now().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def list_research_decisions(self, portfolio_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM research_decisions
                WHERE portfolio_id = ?
                ORDER BY research_date, id
                """,
                (portfolio_id,),
            ).fetchall()


def row_to_bar(row: sqlite3.Row) -> MarketBar:
    return MarketBar(
        symbol=row["symbol"],
        trading_date=parse_date(row["trading_date"]),
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        adjusted_close=row["adjusted_close"],
        volume=row["volume"],
        provider=row["provider"],
        interval=row["interval"],
        price_basis=row["price_basis"],
    )


def to_stooq_symbol(symbol: str) -> str:
    return f"{symbol.lower()}.us"


def to_yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")
