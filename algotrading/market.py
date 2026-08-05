from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from .db import Database, utc_now
from .models import FetchResult, MarketBar
from .providers import MarketDataProvider
from .universe import normalize_symbol


class MarketService:
    def __init__(self, db: Database, provider: MarketDataProvider):
        self.db = db
        self.provider = provider

    def initialize(self) -> None:
        self.db.initialize()

    def sync(
        self,
        symbols: list[str],
        start: date,
        end: date,
        force: bool = False,
        progress=None,
        continue_on_error: bool = False,
        cancelled=None,
    ) -> dict[str, int]:
        if start > end:
            raise ValueError("start date must be on or before end date")
        self.db.initialize()
        results: dict[str, int] = {}
        self.errors = {}
        for raw_symbol in dict.fromkeys(symbols):
            if cancelled and cancelled():
                from .simulation import BacktestCancelled

                raise BacktestCancelled("Market sync cancelled")
            symbol = self.db.require_symbol(raw_symbol)
            symbol_row = self.db.get_symbol(symbol)
            provider_symbol = symbol_row["provider_symbol"] if symbol_row else None
            if progress:
                progress({"symbol": symbol, "status": "started", "message": f"syncing {symbol}"})
            if not force and self._covered(symbol, start, end):
                results[symbol] = 0
                if progress:
                    progress(
                        {
                            "symbol": symbol,
                            "status": "covered",
                            "rows": 0,
                            "message": f"{symbol} already covered",
                        }
                    )
                continue
            try:
                result = self.provider.fetch_daily(
                    symbol, start, end, provider_symbol=provider_symbol
                )
            except Exception as error:
                result = FetchResult(
                    self.provider.name,
                    symbol,
                    provider_symbol or symbol,
                    start,
                    end,
                    [],
                    utc_now(),
                    False,
                    str(error),
                )
            if result.success:
                try:
                    if any(
                        bar.symbol != symbol
                        or bar.provider != self.provider.name
                        or bar.interval != "1d"
                        or not start <= bar.trading_date <= end
                        for bar in result.bars
                    ):
                        raise ValueError(
                            "provider returned bars outside the requested series or dates"
                        )
                    self.db.insert_market_bars(result.bars, fetched_at=result.fetched_at)
                except ValueError as error:
                    result = replace(result, success=False, error_message=str(error))
            self.db.record_fetch(result)
            if not result.success:
                self.errors[symbol] = result.error_message
                if progress:
                    progress(
                        {
                            "symbol": symbol,
                            "status": "failed",
                            "message": f"{symbol} failed: {result.error_message}",
                        }
                    )
                if continue_on_error:
                    continue
                raise RuntimeError(f"failed to fetch {symbol}: {result.error_message}")
            results[symbol] = len(result.bars)
            if progress:
                progress(
                    {
                        "symbol": symbol,
                        "status": "completed",
                        "rows": len(result.bars),
                        "message": f"{symbol} synced {len(result.bars)} rows",
                    }
                )
        return results

    def refresh(self, symbols: list[str], days: int) -> dict[str, int]:
        if days < 1:
            raise ValueError("days must be positive")
        end = date.today()
        start = end - timedelta(days=days)
        return self.sync(symbols, start, end, force=True)

    def bars(self, symbol: str, start: date, end: date) -> list[MarketBar]:
        canonical = self.db.require_symbol(symbol)
        return self.db.get_bars(canonical, start, end, provider=self.provider.name)

    def price(self, symbol: str, target_date: date, allow_previous: bool = True) -> MarketBar:
        canonical = self.db.require_symbol(symbol)
        if allow_previous:
            bar = self.db.get_bar_on_or_before(canonical, target_date, provider=self.provider.name)
        else:
            found = self.db.get_bars(
                canonical, target_date, target_date, provider=self.provider.name
            )
            bar = found[0] if found else None
        if bar is None:
            raise LookupError(f"no stored market data for {canonical} on or before {target_date}")
        return bar

    def replay(self, start: date, end: date) -> list[tuple[date, list[MarketBar]]]:
        if start > end:
            raise ValueError("start date must be on or before end date")
        by_date: dict[date, list[MarketBar]] = {}
        self.db.initialize()
        for row in self.db.list_symbols():
            symbol = row["symbol"]
            for bar in self.db.get_bars(symbol, start, end, provider=self.provider.name):
                by_date.setdefault(bar.trading_date, []).append(bar)
        return [(trading_date, by_date[trading_date]) for trading_date in sorted(by_date)]

    def list_symbols(self) -> list[str]:
        self.db.initialize()
        return [row["symbol"] for row in self.db.list_symbols()]

    def add_symbol(
        self,
        symbol: str,
        provider_symbol: str | None = None,
        asset_name: str | None = None,
        asset_type: str = "equity",
    ) -> str:
        self.db.initialize()
        return self.db.add_symbol(
            normalize_symbol(symbol),
            provider_symbol=provider_symbol,
            asset_name=asset_name,
            asset_type=asset_type,
        )

    def remove_symbol(self, symbol: str) -> str:
        self.db.initialize()
        return self.db.deactivate_symbol(symbol)

    def _covered(self, symbol: str, start: date, end: date) -> bool:
        coverage = self.db.get_coverage(symbol, provider=self.provider.name)
        if coverage is None:
            return False
        covered_start, covered_end = coverage
        if covered_start <= start and covered_end >= end:
            return True
        with self.db.connect() as conn:
            return (
                conn.execute(
                    """SELECT 1 FROM provider_fetches WHERE symbol=? AND provider=?
                AND success=1 AND fetched_row_count>0 AND requested_start<=? AND requested_end>=?
                AND substr(fetched_at, 1, 10)>? LIMIT 1""",
                    (symbol, self.provider.name, str(start), str(end), str(end)),
                ).fetchone()
                is not None
            )
