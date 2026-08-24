from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from math import isfinite, tanh
from pathlib import Path

from .models import MarketBar

MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_NAMES = ("nn_stock_pattern_v1", "nn_sector_rotation_v1")
FEATURE_NAMES = (
    "return_5d",
    "return_21d",
    "return_63d",
    "volatility_21d",
    "rsi_14",
    "drawdown_63d",
    "volume_trend_21d",
)

SECTOR_BY_SYMBOL = {
    "SPY": "broad-market",
    "QQQ": "technology",
    "DIA": "broad-market",
    "IWM": "small-cap",
    "VTI": "broad-market",
    "VOO": "broad-market",
    "XLK": "technology",
    "XLF": "financials",
    "XLE": "energy",
    "XLV": "healthcare",
    "XLY": "consumer-discretionary",
    "XLP": "consumer-staples",
    "XLI": "industrials",
    "XLU": "utilities",
    "XLB": "materials",
    "XLRE": "real-estate",
    "AAPL": "technology",
    "ACN": "technology",
    "ADBE": "technology",
    "ADI": "technology",
    "AMAT": "technology",
    "AMD": "technology",
    "ANET": "technology",
    "CDNS": "technology",
    "CRM": "technology",
    "CRWD": "technology",
    "CSCO": "technology",
    "IBM": "technology",
    "INTC": "technology",
    "KLAC": "technology",
    "LRCX": "technology",
    "MCHP": "technology",
    "MRVL": "technology",
    "MSFT": "technology",
    "MU": "technology",
    "NET": "technology",
    "NOW": "technology",
    "NVDA": "technology",
    "ORCL": "technology",
    "PANW": "technology",
    "PLTR": "technology",
    "QCOM": "technology",
    "SNPS": "technology",
    "TEAM": "technology",
    "TXN": "technology",
    "ZS": "technology",
    "AMZN": "consumer-discretionary",
    "AZO": "consumer-discretionary",
    "BKNG": "consumer-discretionary",
    "CCL": "consumer-discretionary",
    "CMG": "consumer-discretionary",
    "DAL": "consumer-discretionary",
    "DIS": "consumer-discretionary",
    "EA": "consumer-discretionary",
    "F": "consumer-discretionary",
    "GM": "consumer-discretionary",
    "HD": "consumer-discretionary",
    "HLT": "consumer-discretionary",
    "LOW": "consumer-discretionary",
    "LULU": "consumer-discretionary",
    "LUV": "consumer-discretionary",
    "MAR": "consumer-discretionary",
    "MCD": "consumer-discretionary",
    "NKE": "consumer-discretionary",
    "ORLY": "consumer-discretionary",
    "RCL": "consumer-discretionary",
    "ROKU": "consumer-discretionary",
    "ROST": "consumer-discretionary",
    "SBUX": "consumer-discretionary",
    "TJX": "consumer-discretionary",
    "TSLA": "consumer-discretionary",
    "TTWO": "consumer-discretionary",
    "UAL": "consumer-discretionary",
    "YUM": "consumer-discretionary",
    "BABA": "communication-services",
    "CHTR": "communication-services",
    "CMCSA": "communication-services",
    "GOOG": "communication-services",
    "GOOGL": "communication-services",
    "META": "communication-services",
    "NFLX": "communication-services",
    "PINS": "communication-services",
    "SNAP": "communication-services",
    "T": "communication-services",
    "TMUS": "communication-services",
    "VZ": "communication-services",
    "WBD": "communication-services",
    "BAC": "financials",
    "BLK": "financials",
    "BRK.B": "financials",
    "BX": "financials",
    "C": "financials",
    "CB": "financials",
    "CME": "financials",
    "COF": "financials",
    "GS": "financials",
    "ICE": "financials",
    "JPM": "financials",
    "MA": "financials",
    "MCO": "financials",
    "MS": "financials",
    "PNC": "financials",
    "SCHW": "financials",
    "SPGI": "financials",
    "TFC": "financials",
    "TRV": "financials",
    "USB": "financials",
    "V": "financials",
    "WFC": "financials",
    "ABBV": "healthcare",
    "ABT": "healthcare",
    "AMGN": "healthcare",
    "BMY": "healthcare",
    "CI": "healthcare",
    "CVS": "healthcare",
    "DHR": "healthcare",
    "ELV": "healthcare",
    "GILD": "healthcare",
    "HUM": "healthcare",
    "ISRG": "healthcare",
    "JNJ": "healthcare",
    "LLY": "healthcare",
    "MDT": "healthcare",
    "MRK": "healthcare",
    "PFE": "healthcare",
    "REGN": "healthcare",
    "TMO": "healthcare",
    "UNH": "healthcare",
    "COP": "energy",
    "CVX": "energy",
    "EOG": "energy",
    "HAL": "energy",
    "KMI": "energy",
    "MPC": "energy",
    "OXY": "energy",
    "PSX": "energy",
    "SLB": "energy",
    "VLO": "energy",
    "WMB": "energy",
    "XOM": "energy",
    "AEP": "utilities",
    "DUK": "utilities",
    "ED": "utilities",
    "EXC": "utilities",
    "NEE": "utilities",
    "PEG": "utilities",
    "SO": "utilities",
    "SRE": "utilities",
    "WEC": "utilities",
    "XEL": "utilities",
    "ALB": "materials",
    "APD": "materials",
    "DD": "materials",
    "DOW": "materials",
    "ECL": "materials",
    "FCX": "materials",
    "LIN": "materials",
    "MOS": "materials",
    "NEM": "materials",
    "NUE": "materials",
    "SHW": "materials",
    "AMT": "real-estate",
    "CCI": "real-estate",
    "DLR": "real-estate",
    "EQIX": "real-estate",
    "O": "real-estate",
    "PLD": "real-estate",
    "PSA": "real-estate",
    "SPG": "real-estate",
    "VICI": "real-estate",
    "WELL": "real-estate",
    "BA": "industrials",
    "CAT": "industrials",
    "CSX": "industrials",
    "DE": "industrials",
    "ETN": "industrials",
    "FDX": "industrials",
    "GE": "industrials",
    "HON": "industrials",
    "ITW": "industrials",
    "LHX": "industrials",
    "LMT": "industrials",
    "NOC": "industrials",
    "NSC": "industrials",
    "PH": "industrials",
    "RTX": "industrials",
    "UBER": "industrials",
    "UNP": "industrials",
    "UPS": "industrials",
    "VMC": "industrials",
    "CL": "consumer-staples",
    "COST": "consumer-staples",
    "GIS": "consumer-staples",
    "KHC": "consumer-staples",
    "KMB": "consumer-staples",
    "KO": "consumer-staples",
    "KR": "consumer-staples",
    "MDLZ": "consumer-staples",
    "MO": "consumer-staples",
    "PEP": "consumer-staples",
    "PG": "consumer-staples",
    "PM": "consumer-staples",
    "SYY": "consumer-staples",
    "TGT": "consumer-staples",
    "WMT": "consumer-staples",
}


@lru_cache(maxsize=None)
def load_model(name: str) -> dict:
    if name not in MODEL_NAMES:
        raise ValueError(f"unknown neural model: {name}")
    with (MODEL_DIR / f"{name}.json").open("r", encoding="utf-8") as handle:
        model = json.load(handle)
    validate_model(model)
    return model


def validate_model(model: dict) -> None:
    if tuple(model.get("input_features", [])) != FEATURE_NAMES:
        raise ValueError("neural artifact feature order does not match inference")
    weights = model.get("hidden_weights", [])
    biases = model.get("hidden_bias", [])
    outputs = model.get("output_weights", [])
    if not weights or len(weights) != len(biases) or len(weights) != len(outputs):
        raise ValueError("invalid neural artifact layer dimensions")
    if any(len(row) != len(FEATURE_NAMES) for row in weights):
        raise ValueError("invalid neural artifact input dimensions")
    numbers = [v for row in weights for v in row] + biases + outputs + [model.get("output_bias")]
    if any(not isinstance(v, (int, float)) or not isfinite(v) for v in numbers):
        raise ValueError("neural artifact weights must be finite")
    dates = [
        date.fromisoformat(model[k])
        for k in (
            "trained_from",
            "trained_through",
            "validation_start",
            "validation_end",
            "first_allowed_backtest_start",
        )
    ]
    if not (dates[0] <= dates[1] < dates[2] <= dates[3] < dates[4]):
        raise ValueError("neural artifact has overlapping training, validation, or backtest dates")
    if model.get("feature_window_days") != 63 or model.get("target_horizon_days", 0) <= 0:
        raise ValueError("neural artifact has an incompatible feature window or target horizon")


def model_metadata(name: str) -> dict:
    model = load_model(name)
    metadata = {
        key: model[key]
        for key in (
            "name",
            "trained_from",
            "trained_through",
            "validation_start",
            "validation_end",
            "first_allowed_backtest_start",
            "feature_window_days",
            "target_horizon_days",
            "training_example_count",
            "validation_example_count",
            "training_loss",
            "validation_loss",
        )
        if key in model
    }
    if "first_allowed_backtest_start" in metadata:
        metadata["first_allowed_backtest_start_date"] = date.fromisoformat(
            metadata["first_allowed_backtest_start"]
        )
    return metadata


def neural_score(
    model_name: str, bars: list[MarketBar], as_of: date | None = None, registry=None
) -> float | None:
    if model_name not in MODEL_NAMES:
        raise ValueError(f"unknown neural model: {model_name}")
    if as_of is not None and bars and bars[-1].trading_date > as_of:
        bars = [bar for bar in bars if bar.trading_date <= as_of]
    features = neural_features(bars)
    if features is None:
        return None
    from .walk_forward import score

    return score(model_name, features, as_of or bars[-1].trading_date, registry)


def neural_features(bars: list[MarketBar]) -> list[float] | None:
    if len(bars) < 64:
        return None
    bars = bars[-64:]
    prices = [_bar_price(bar) for bar in bars]
    volumes = [max(1, bar.volume) for bar in bars]
    if min(prices[-64:]) <= 0:
        return None
    returns_21 = _returns(prices[-22:])
    return [
        _clamp(_return(prices, 5) * 5.0),
        _clamp(_return(prices, 21) * 2.5),
        _clamp(_return(prices, 63) * 1.5),
        _clamp(_stddev(returns_21) * 20.0),
        _clamp((_rsi(prices[-15:]) - 50.0) / 50.0),
        _clamp((prices[-1] / max(prices[-63:]) - 1.0) * 3.0),
        _clamp((sum(volumes[-5:]) / 5) / (sum(volumes[-21:]) / 21) - 1.0),
    ]


def sector_for_symbol(symbol: str, provider_sector: str | None = None) -> str:
    aliases = {
        "Technology": "technology",
        "Health Care": "healthcare",
        "Finance": "financials",
        "Energy": "energy",
        "Utilities": "utilities",
        "Industrials": "industrials",
        "Consumer Discretionary": "consumer-discretionary",
        "Consumer Staples": "consumer-staples",
        "Real Estate": "real-estate",
        "Basic Materials": "materials",
        "Telecommunications": "communication-services",
    }
    return SECTOR_BY_SYMBOL.get(symbol, aliases.get(provider_sector, provider_sector or "other"))


def _bar_price(bar: MarketBar) -> float:
    return bar.adjusted_close if bar.adjusted_close is not None else bar.close


def _return(prices: list[float], days: int) -> float:
    return prices[-1] / prices[-days - 1] - 1.0


def _returns(prices: list[float]) -> list[float]:
    return [
        prices[index] / prices[index - 1] - 1.0
        for index in range(1, len(prices))
        if prices[index - 1] != 0
    ]


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return variance**0.5


def _rsi(prices: list[float]) -> float:
    changes = [prices[index] - prices[index - 1] for index in range(1, len(prices))]
    gains = sum(max(change, 0.0) for change in changes) / len(changes)
    losses = sum(max(-change, 0.0) for change in changes) / len(changes)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + gains / losses))


def _tanh(value: float) -> float:
    return tanh(value)


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
