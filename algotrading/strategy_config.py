"""One strategy catalog and parameter contract for the CLI and the web UI."""

from math import isfinite

PARAMETERS = {
    "short_window": (int, 1, 2520),
    "long_window": (int, 2, 2520),
    "lookback_days": (int, 1, 2520),
    "rebalance_days": (int, 1, 2520),
    "top_n": (int, 1, 1000),
    "sector_count": (int, 1, 30),
    "research_interval_days": (int, 1, 2520),
    "agent_max_researches": (int, 1, 20),
    "agent_online_research": (int, 0, 1),
    "seed": (int, 0, 2147483647),
    "commission": (float, 0, 10000),
    "slippage_bps": (float, 0, 1000),
    "max_position_pct": (float, 1, 100),
    "cash_reserve_pct": (float, 0, 95),
    "rebalance_tolerance_pct": (float, 0, 20),
    "risk_free_rate": (float, 0, 1),
    "skip_days": (int, 0, 252),
}

CATALOG = {
    "buy-and-hold": ("Buy & hold", "Baseline", {"symbols": "SPY"}),
    "equal-weight": ("Equal weight", "Allocation", {"rebalance_days": 21}),
    "moving-average": (
        "Moving average",
        "Trend",
        {"symbols": "SPY", "short_window": 50, "long_window": 200},
    ),
    "momentum": (
        "Relative momentum",
        "Momentum",
        {"lookback_days": 126, "rebalance_days": 21, "top_n": 10},
    ),
    "mean-reversion": (
        "Short-term reversal",
        "Experimental",
        {"lookback_days": 21, "rebalance_days": 5, "top_n": 10},
    ),
    "low-volatility": (
        "Low volatility",
        "Defensive",
        {"lookback_days": 63, "rebalance_days": 21, "top_n": 10},
    ),
    "breakout": (
        "Price breakout",
        "Trend",
        {"lookback_days": 55, "rebalance_days": 5, "top_n": 10},
    ),
    "trend-following": (
        "Trend following",
        "Trend",
        {"lookback_days": 126, "rebalance_days": 21, "top_n": 10},
    ),
    "rsi-strength": (
        "RSI strength",
        "Momentum",
        {"lookback_days": 14, "rebalance_days": 21, "top_n": 10},
    ),
    "dual-momentum": (
        "Dual momentum",
        "Trend",
        {
            "symbols": "SPY QQQ IWM EFA EEM TLT GLD",
            "lookback_days": 252,
            "skip_days": 21,
            "rebalance_days": 21,
            "top_n": 3,
        },
    ),
    "time-series-momentum": (
        "Time-series momentum",
        "Trend",
        {"symbols": "SPY EFA EEM TLT GLD", "lookback_days": 252, "rebalance_days": 21},
    ),
    "inverse-volatility": (
        "Inverse volatility",
        "Allocation",
        {"symbols": "SPY TLT GLD", "lookback_days": 63, "rebalance_days": 21},
    ),
    "hybrid": (
        "Adaptive ensemble",
        "Experimental",
        {"lookback_days": 63, "rebalance_days": 21, "top_n": 10, "seed": 42},
    ),
    "nn-pattern": (
        "Neural patterns",
        "Experimental",
        {"lookback_days": 63, "rebalance_days": 21, "top_n": 10},
    ),
    "nn-sector-rotation": (
        "Neural sector rotation",
        "Experimental",
        {"lookback_days": 63, "rebalance_days": 21, "top_n": 10, "sector_count": 3},
    ),
    "nn-risk-adjusted": (
        "Neural risk-adjusted",
        "Experimental",
        {"lookback_days": 63, "rebalance_days": 21, "top_n": 10},
    ),
    "agentic-research": (
        "Stock discovery",
        "Experimental",
        {
            "symbols": "SPY",
            "lookback_days": 63,
            "research_interval_days": 63,
            "agent_max_researches": 4,
            "top_n": 5,
        },
    ),
}


def validate_parameters(strategy, parameters=None):
    result = {k: v for k, v in CATALOG[strategy][2].items() if k != "symbols"}
    result.update(
        {
            "commission": 1.0,
            "slippage_bps": 5.0,
            "max_position_pct": 100.0,
            "cash_reserve_pct": 0.0,
            "rebalance_tolerance_pct": 0.0,
            "seed": 42,
            "risk_free_rate": 0.0,
        }
    )
    for key, value in (parameters or {}).items():
        if key not in PARAMETERS:
            raise ValueError(f"unknown parameter: {key}")
        if value in (None, ""):
            continue
        kind, minimum, maximum = PARAMETERS[key]
        if isinstance(value, bool):
            raise ValueError(f"{key} must be numeric")
        numeric = float(value)
        if (
            not isfinite(numeric)
            or numeric < minimum
            or numeric > maximum
            or (kind is int and not numeric.is_integer())
        ):
            raise ValueError(
                f"{key} must be {'an integer ' if kind is int else ''}between {minimum} and {maximum}"
            )
        result[key] = kind(numeric)
    if strategy == "moving-average" and result["short_window"] >= result["long_window"]:
        raise ValueError("moving-average requires short_window < long_window")
    if strategy == "dual-momentum" and result["skip_days"] >= result["lookback_days"]:
        raise ValueError("skip_days must be less than lookback_days")
    if (
        strategy
        in {
            "low-volatility",
            "breakout",
            "trend-following",
            "rsi-strength",
            "inverse-volatility",
            "time-series-momentum",
        }
        and result["lookback_days"] < 2
    ):
        raise ValueError("lookback_days must be at least 2")
    return result
