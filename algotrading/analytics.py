"""Trading-session statistics, without weekend dilution or hidden first-day losses."""

from __future__ import annotations

from math import sqrt
from statistics import stdev


def performance_metrics(
    points: list[dict], starting_value: float, risk_free_rate: float = 0.0
) -> dict:
    values = [float(p["total_value"]) for p in points]
    previous = starting_value
    returns = []
    peak = starting_value
    drawdowns = []
    for value in values:
        returns.append(value / previous - 1 if previous else 0.0)
        previous = value
        peak = max(peak, value)
        drawdowns.append(value / peak - 1 if peak else 0.0)
    end = values[-1] if values else starting_value
    total_return = end / starting_value - 1 if starting_value else 0.0
    count = len(returns)
    daily_rf = (1 + risk_free_rate) ** (1 / 252) - 1
    excess = [r - daily_rf for r in returns]
    avg = sum(excess) / count if count else 0.0
    deviation = stdev(returns) if count > 1 else 0.0
    downside = sqrt(sum(min(r, 0.0) ** 2 for r in excess) / count) if count else 0.0
    annualized = (
        ((end / starting_value) ** (252 / count) - 1) if count >= 252 and starting_value else None
    )
    return {
        "start_value": starting_value,
        "end_value": end,
        "total_return": total_return,
        "cagr": annualized,
        "max_drawdown": min(drawdowns, default=0.0),
        "volatility": deviation * sqrt(252),
        "sharpe": avg / deviation * sqrt(252) if deviation else None,
        "sortino": avg / downside * sqrt(252) if downside else None,
        "trading_days": count,
    }


def enrich_curve(points: list[dict], starting_cash: float) -> list[dict]:
    peak = starting_cash
    result = []
    for point in points:
        peak = max(peak, point["total_value"])
        result.append(
            {
                **point,
                "drawdown": point["total_value"] / peak - 1 if peak else 0,
                "return": point["total_value"] / starting_cash - 1 if starting_cash else 0,
            }
        )
    return result


def monthly_returns(points: list[dict], starting_cash: float) -> list[dict]:
    closes = {}
    for point in points:
        closes[str(point["date"])[:7]] = point["total_value"]
    previous = starting_cash
    result = []
    for month, value in closes.items():
        result.append({"month": month, "return": value / previous - 1 if previous else 0.0})
        previous = value
    return result
