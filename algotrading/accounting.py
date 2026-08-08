"""Shared average-cost ledger for manual portfolios and the backtest broker."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Position:
    shares: int
    average_cost: float


class Ledger:
    def __init__(self, starting_cash: float):
        if not isfinite(starting_cash) or starting_cash < 0:
            raise ValueError("starting cash must be finite and nonnegative")
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.holdings: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.realized_by_symbol: dict[str, float] = {}
        self.fees = 0.0
        self.slippage = 0.0
        self.sell_pnls: list[float] = []

    def apply(self, trade) -> None:
        if isinstance(trade.shares, bool) or not isinstance(trade.shares, int) or trade.shares <= 0:
            raise ValueError("shares must be a positive integer")
        if not isfinite(trade.execution_price) or trade.execution_price <= 0:
            raise ValueError("execution price must be positive and finite")
        if not isfinite(trade.cash_impact):
            raise ValueError("cash impact must be finite")
        position = self.holdings.get(trade.symbol, Position(0, 0.0))
        gross = trade.execution_price * trade.shares
        if trade.side == "buy":
            fees = -trade.cash_impact - gross
            if self.cash + trade.cash_impact < -1e-7:
                raise ValueError("insufficient cash")
            shares = position.shares + trade.shares
            updated = Position(
                shares, (position.shares * position.average_cost - trade.cash_impact) / shares
            )
            pnl = 0.0
        elif trade.side == "sell":
            fees = gross - trade.cash_impact
            if position.shares < trade.shares:
                raise ValueError("insufficient shares")
            updated = Position(position.shares - trade.shares, position.average_cost)
            pnl = trade.cash_impact - position.average_cost * trade.shares
        else:
            raise ValueError("side must be buy or sell")
        if fees < -1e-7:
            raise ValueError("cash impact does not reconcile with execution price")
        if not isfinite(trade.fees) or abs(trade.fees - fees) > 1e-6:
            raise ValueError("recorded fee does not match the cash ledger")
        if not isfinite(trade.slippage) or trade.slippage < 0:
            raise ValueError("slippage must be finite and nonnegative")
        self.cash = max(0.0, self.cash + trade.cash_impact)
        self.fees += max(0.0, fees)
        self.slippage += getattr(trade, "slippage", 0.0)
        if updated.shares:
            self.holdings[trade.symbol] = updated
        else:
            self.holdings.pop(trade.symbol, None)
        self.realized_pnl += pnl
        self.realized_by_symbol[trade.symbol] = self.realized_by_symbol.get(trade.symbol, 0.0) + pnl
        if trade.side == "sell":
            self.sell_pnls.append(pnl)

    def mark(self, prices: dict[str, float]) -> tuple[float, float, float]:
        market = sum(p.shares * prices[s] for s, p in self.holdings.items())
        cost = sum(p.shares * p.average_cost for p in self.holdings.values())
        total = self.cash + market
        unrealized = market - cost
        if abs(total - self.starting_cash - self.realized_pnl - unrealized) > max(
            1e-6, total * 1e-10
        ):
            raise ArithmeticError("portfolio accounting failed to reconcile")
        return market, total, unrealized
