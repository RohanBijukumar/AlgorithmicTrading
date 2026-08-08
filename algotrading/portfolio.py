from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt

from .accounting import Ledger, Position
from .analytics import performance_metrics
from .db import Database, utc_now
from .market_window import MarketWindow
from .models import Trade


@dataclass(frozen=True)
class PortfolioState:
    portfolio_id: int
    name: str
    cash: float
    holdings: dict[str, Position]
    realized_pnl: float


@dataclass(frozen=True)
class PortfolioValuation:
    state: PortfolioState
    valuation_date: date
    market_value: float
    total_value: float
    unrealized_pnl: float
    holding_values: dict[str, float]


@dataclass(frozen=True)
class PortfolioValuePoint:
    valuation_date: date
    cash: float
    market_value: float
    total_value: float
    realized_pnl: float
    unrealized_pnl: float


class PortfolioService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, name: str, starting_cash: float) -> int:
        self.db.initialize()
        return self.db.create_portfolio(name, starting_cash)

    def buy(self, portfolio_name: str, symbol: str, shares: int, execution_date: date) -> int:
        return self._trade(portfolio_name, symbol, "buy", shares, execution_date)

    def sell(self, portfolio_name: str, symbol: str, shares: int, execution_date: date) -> int:
        return self._trade(portfolio_name, symbol, "sell", shares, execution_date)

    def state(self, portfolio_name: str, through: date | None = None) -> PortfolioState:
        portfolio = self._portfolio_or_raise(portfolio_name)
        ledger = Ledger(float(portfolio["starting_cash"]))
        for trade in self.db.get_trades(portfolio["id"], through=through):
            ledger.apply(trade)
        return PortfolioState(
            portfolio_id=portfolio["id"],
            name=portfolio["name"],
            cash=ledger.cash,
            holdings=ledger.holdings,
            realized_pnl=ledger.realized_pnl,
        )

    def value(self, portfolio_name: str, valuation_date: date) -> PortfolioValuation:
        state = self.state(portfolio_name, through=valuation_date)
        snapshots = self.db.get_snapshots(state.portfolio_id)
        if snapshots:
            saved = [p for p in snapshots if p["valuation_date"] <= str(valuation_date)]
            if saved:
                point = saved[-1]
                # Freeze completed backtests; subsequent data refreshes must not change their results.
                actual_date = date.fromisoformat(point["valuation_date"])
                state = self.state(portfolio_name, through=actual_date)
                holdings = json.loads(point["holding_values_json"])
                if not holdings and state.holdings:
                    holdings = {}
                    for symbol, position in state.holdings.items():
                        bar = self.db.get_bar_on_or_before(symbol, actual_date)
                        if bar is not None:
                            holdings[symbol] = (
                                bar.adjusted_close if bar.adjusted_close is not None else bar.close
                            ) * position.shares
                return PortfolioValuation(
                    state,
                    actual_date,
                    point["market_value"],
                    point["total_value"],
                    point["unrealized_pnl"],
                    holdings,
                )
        market_value = 0.0
        unrealized_pnl = 0.0
        holding_values: dict[str, float] = {}
        for symbol, position in state.holdings.items():
            bar = self.db.get_bar_on_or_before(symbol, valuation_date)
            if bar is None:
                raise LookupError(
                    f"no stored market data for {symbol} on or before {valuation_date}"
                )
            price = bar.adjusted_close if bar.adjusted_close is not None else bar.close
            holding_market_value = price * position.shares
            holding_values[symbol] = holding_market_value
            market_value += holding_market_value
            unrealized_pnl += (price - position.average_cost) * position.shares
        return PortfolioValuation(
            state=state,
            valuation_date=valuation_date,
            market_value=market_value,
            total_value=state.cash + market_value,
            unrealized_pnl=unrealized_pnl,
            holding_values=holding_values,
        )

    def performance(self, portfolio_name: str, start: date, end: date) -> dict[str, float]:
        if start > end:
            raise ValueError("start date must be on or before end date")
        start_value = self.value(portfolio_name, start - timedelta(days=1)).total_value
        points = self.value_history(portfolio_name, end=end, start=start, max_points=100000)
        sessions = set(self.db.trading_dates(start, end))
        return performance_metrics(
            [{"total_value": p.total_value} for p in points if p.valuation_date in sessions],
            start_value,
        )

    def value_history(
        self,
        portfolio_name: str,
        end: date,
        start: date | None = None,
        max_points: int = 260,
    ) -> list[PortfolioValuePoint]:
        if max_points <= 0:
            raise ValueError("max_points must be positive")
        portfolio = self._portfolio_or_raise(portfolio_name)
        trades = self.db.get_trades(portfolio["id"], through=end)
        if start is None:
            start = trades[0].execution_date if trades else end
        if start > end:
            raise ValueError("start date must be on or before end date")
        saved = self.db.get_snapshots(portfolio["id"])
        if saved:
            rows = [p for p in saved if str(start) <= p["valuation_date"] <= str(end)]
            points = [
                PortfolioValuePoint(
                    date.fromisoformat(p["valuation_date"]),
                    p["cash"],
                    p["market_value"],
                    p["total_value"],
                    p["realized_pnl"],
                    p["unrealized_pnl"],
                )
                for p in rows
            ]
            return _sample_points(points, max_points)
        symbols = sorted({trade.symbol for trade in trades})
        dates = self.db.trading_dates(start, end, symbols=symbols or None)
        if not dates:
            dates = [end]
        if dates[-1] < end:
            dates.append(end)

        window = MarketWindow(self.db, symbols, end)
        ledger = Ledger(float(portfolio["starting_cash"]))
        cursor = 0
        points: list[PortfolioValuePoint] = []
        for valuation_date in dates:
            while cursor < len(trades) and trades[cursor].execution_date <= valuation_date:
                ledger.apply(trades[cursor])
                cursor += 1
            prices = {}
            for symbol in ledger.holdings:
                bar = window.get_bar_on_or_before(symbol, valuation_date)
                if bar is None:
                    raise LookupError(f"missing valuation price for {symbol} on {valuation_date}")
                prices[symbol] = bar.adjusted_close if bar.adjusted_close is not None else bar.close
            market, total, unrealized = ledger.mark(prices)
            points.append(
                PortfolioValuePoint(
                    valuation_date=valuation_date,
                    cash=ledger.cash,
                    market_value=market,
                    total_value=total,
                    realized_pnl=ledger.realized_pnl,
                    unrealized_pnl=unrealized,
                )
            )
        return _sample_points(points, max_points)

    def _trade(
        self,
        portfolio_name: str,
        symbol: str,
        side: str,
        shares: int,
        execution_date: date,
    ) -> int:
        if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
            raise ValueError("shares must be a positive integer")
        with self.db.transaction():
            canonical = self.db.require_symbol(symbol)
            portfolio = self._portfolio_or_raise(portfolio_name)
            if self.db.get_snapshots(portfolio["id"]):
                raise ValueError(
                    "backtest portfolios are read-only; create a manual portfolio to place trades"
                )
            bars = self.db.get_bars(canonical, execution_date, execution_date)
            if not bars:
                raise LookupError(
                    f"no exact trading-session price for {canonical} on {execution_date}"
                )
            bar = bars[0]
            trades = self.db.get_trades(portfolio["id"])
            if trades and execution_date < trades[-1].execution_date:
                raise ValueError(
                    "trade date must be on or after the latest trade; backdated inserts can invalidate the ledger"
                )
            ledger = Ledger(float(portfolio["starting_cash"]))
            for trade in trades:
                ledger.apply(trade)
            price = bar.adjusted_close if bar.adjusted_close is not None else bar.close
            impact = price * shares * (-1 if side == "buy" else 1)
            ledger.apply(
                Trade(
                    0,
                    portfolio["id"],
                    canonical,
                    side,
                    shares,
                    execution_date,
                    price,
                    impact,
                    utc_now(),
                )
            )
            return self.db.insert_trade(
                portfolio["id"], canonical, side, shares, execution_date, price, impact
            )

    def _portfolio_or_raise(self, name: str):
        portfolio = self.db.get_portfolio(name)
        if portfolio is None:
            raise LookupError(f"portfolio not found: {name}")
        return portfolio

    def audit(self, portfolio_name: str, valuation_date: date) -> dict:
        portfolio = self._portfolio_or_raise(portfolio_name)
        ledger = Ledger(float(portfolio["starting_cash"]))
        trades = self.db.get_trades(portfolio["id"], through=valuation_date)
        for trade in trades:
            ledger.apply(trade)
        value = self.value(portfolio_name, valuation_date)
        difference = (
            value.total_value - ledger.starting_cash - ledger.realized_pnl - value.unrealized_pnl
        )
        holdings_difference = value.market_value - sum(value.holding_values.values())
        return {
            "portfolio": portfolio_name,
            "trades": len(trades),
            "cash": ledger.cash,
            "starting_cash": ledger.starting_cash,
            "market_value": value.market_value,
            "total_value": value.total_value,
            "realized_pnl": ledger.realized_pnl,
            "unrealized_pnl": value.unrealized_pnl,
            "fees": ledger.fees,
            "difference": difference,
            "holdings_difference": holdings_difference,
            "reconciled": abs(difference) < 1e-6
            and abs(holdings_difference) < 1e-6
            and ledger.cash >= 0,
        }


def _sample_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(variance)


def _max_drawdown(values: list[float]) -> float:
    peak = None
    worst = 0.0
    for value in values:
        if peak is None or value > peak:
            peak = value
        if peak:
            worst = min(worst, value / peak - 1.0)
    return worst


def _sample_points(points, limit):
    if len(points) <= limit:
        return points
    if limit == 1:
        return [points[-1]]
    return [points[round(i * (len(points) - 1) / (limit - 1))] for i in range(limit)]
