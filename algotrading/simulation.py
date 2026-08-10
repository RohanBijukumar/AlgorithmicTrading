"""In-memory, long-only broker. Orders expire after the next observed session."""

from __future__ import annotations

from datetime import timedelta
from math import floor

from .accounting import Ledger
from .db import utc_now
from .models import Trade
from .portfolio import PortfolioState, PortfolioValuation


class BacktestCancelled(Exception):
    pass


def adjusted_price(bar):
    return bar.adjusted_close if bar.adjusted_close is not None else bar.close


class SimulationBroker:
    def __init__(self, market, portfolio_id, name, starting_cash, parameters):
        self.market = market
        self.portfolio_id = portfolio_id
        self.name = name
        self.ledger = Ledger(starting_cash)
        self.parameters = parameters
        self.trades = []
        self.rejections = []

    def state(self, portfolio_name=None, through=None):
        return PortfolioState(
            self.portfolio_id,
            self.name,
            self.ledger.cash,
            dict(self.ledger.holdings),
            self.ledger.realized_pnl,
        )

    def value(self, portfolio_name, valuation_date):
        prices = {}
        for symbol in self.ledger.holdings:
            bar = self.market.get_bar_on_or_before(symbol, valuation_date)
            if bar is None:
                raise LookupError(f"missing valuation price for {symbol}")
            prices[symbol] = adjusted_price(bar)
        market, total, unrealized = self.ledger.mark(prices)
        return PortfolioValuation(
            self.state(),
            valuation_date,
            market,
            total,
            unrealized,
            {s: p.shares * prices[s] for s, p in self.ledger.holdings.items()},
        )

    def execute(self, orders, execution_date, progress=None):
        for order in sorted(orders, key=lambda o: o.side == "buy"):
            if order.shares <= 0:
                continue
            bars = self.market.get_bars(order.symbol, execution_date, execution_date)
            if not bars:
                self.reject(order, execution_date, "no price for execution session", progress)
                continue
            bar = bars[0]
            reference = bar.open * adjusted_price(bar) / bar.close
            slip_rate = self.parameters.get("slippage_bps", 5.0) / 10000
            price = reference * (1 + slip_rate if order.side == "buy" else 1 - slip_rate)
            fee = self.parameters.get("commission", 1.0)
            shares = order.shares
            if order.side == "buy":
                # Open marks keep sizing independent of this session's closing price.
                equity = self.ledger.cash
                for symbol, position in self.ledger.holdings.items():
                    current = self.market.get_bars(symbol, execution_date, execution_date)
                    old = self.market.get_bar_on_or_before(
                        symbol, execution_date - timedelta(days=1)
                    )
                    mark = (
                        current[0].open * adjusted_price(current[0]) / current[0].close
                        if current
                        else adjusted_price(old)
                    )
                    equity += mark * position.shares
                reserve = equity * self.parameters.get("cash_reserve_pct", 0) / 100
                cap = equity * self.parameters.get("max_position_pct", 100) / 100
                held = self.ledger.holdings.get(order.symbol)
                shares = min(
                    shares,
                    max(0, floor((self.ledger.cash - fee - reserve + 1e-9) / price)),
                    max(0, floor(cap / price) - (held.shares if held else 0)),
                )
            elif order.side == "sell":
                held = self.ledger.holdings.get(order.symbol)
                shares = min(shares, held.shares if held else 0)
            else:
                raise ValueError(f"unknown order side: {order.side}")
            if shares <= 0 or (order.side == "sell" and shares * price <= fee):
                self.reject(order, execution_date, "cash, fee, or position limit", progress)
                continue
            impact = (-shares * price if order.side == "buy" else shares * price) - fee
            trade = Trade(
                len(self.trades) + 1,
                self.portfolio_id,
                order.symbol,
                order.side,
                shares,
                execution_date,
                price,
                impact,
                utc_now(),
                fee,
                abs(price - reference) * shares,
            )
            self.ledger.apply(trade)
            self.trades.append(trade)
            if progress:
                progress(
                    {
                        "type": "trade",
                        "date": str(execution_date),
                        "symbol": order.symbol,
                        "side": order.side,
                        "shares": shares,
                        "execution_price": price,
                        "fees": fee,
                        "message": f"{execution_date} {order.side.upper()} {shares} {order.symbol} @ ${price:,.2f}; fee ${fee:.2f}"
                        + (f" (resized from {order.shares})" if shares != order.shares else ""),
                    }
                )

    def reject(self, order, execution_date, reason, progress):
        event = {
            "type": "order_rejected",
            "date": str(execution_date),
            "symbol": order.symbol,
            "message": f"{execution_date} SKIPPED {order.side.upper()} {order.symbol}: {reason}",
        }
        self.rejections.append(event)
        if progress:
            progress(event)

    def persist(self, db, points):
        for trade in self.trades:
            db.insert_trade(
                self.portfolio_id,
                trade.symbol,
                trade.side,
                trade.shares,
                trade.execution_date,
                trade.execution_price,
                trade.cash_impact,
                trade.fees,
                trade.slippage,
            )
        db.save_snapshots(self.portfolio_id, points)
