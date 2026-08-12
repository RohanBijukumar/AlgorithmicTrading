from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from math import floor
from random import Random
from typing import Callable

from .db import Database
from .models import MarketBar
from .nn_models import neural_score, sector_for_symbol
from .portfolio import PortfolioService
from .providers import YahooChartMarketDataProvider
from .strategy_config import CATALOG
from .universe import normalize_symbol

UNIVERSE_KEYWORDS = {"@UNIVERSE", "UNIVERSE"}
ProgressCallback = Callable[[dict], None]

AGENTIC_RESEARCH_CANDIDATES = (
    "APP",
    "ARM",
    "CEG",
    "VRT",
    "HOOD",
    "RDDT",
    "RKLB",
    "HIMS",
    "COIN",
    "TTD",
    "DDOG",
    "SNOW",
    "MDB",
    "SHOP",
    "SE",
    "NU",
    "TSM",
    "ASML",
    "NVO",
    "MELI",
    "CRWD",
    "NET",
    "PANW",
    "PLTR",
    "UBER",
    "ABNB",
    "RBLX",
    "TOST",
    "MSTR",
    "SMCI",
)


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str
    shares: int


@dataclass(frozen=True)
class StrategyContext:
    db: Database
    portfolio: PortfolioService
    portfolio_name: str
    current_date: date
    symbols: list[str]
    start: date
    end: date
    parameters: dict
    progress: ProgressCallback | None = None


@dataclass(frozen=True)
class BacktestResult:
    run_id: int
    portfolio_name: str
    strategy_name: str
    start: date
    end: date
    trades_executed: int
    metrics: dict[str, float]
    benchmark: dict[str, float | str | None]


class Strategy:
    name: str
    description: str

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        raise NotImplementedError


class BuyAndHoldStrategy(Strategy):
    name = "buy-and-hold"
    description = "Invest starting cash equally across selected symbols on the first trading date."

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        trading_dates = _trading_dates(
            context.db, context.symbols, context.start, context.current_date
        )
        if not trading_dates or context.current_date != trading_dates[0]:
            return []
        return _target_equal_weight_orders(context)


class EqualWeightStrategy(Strategy):
    name = "equal-weight"
    description = "Rebalance equally across selected symbols every N trading days."

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        interval = int(context.parameters.get("rebalance_days", 21))
        trading_dates = _trading_dates(
            context.db, context.symbols, context.start, context.current_date
        )
        if not trading_dates:
            return []
        day_index = len(trading_dates) - 1
        if day_index % interval != 0 and context.current_date != context.start:
            return []
        return _target_equal_weight_orders(context)


class MovingAverageCrossoverStrategy(Strategy):
    name = "moving-average"
    description = (
        "For one symbol, hold shares when short moving average is above long moving average."
    )

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        symbol = context.symbols[0]
        short_window = int(context.parameters.get("short_window", 20))
        long_window = int(context.parameters.get("long_window", 50))
        if short_window <= 0 or long_window <= 0 or short_window >= long_window:
            raise ValueError("moving-average requires 0 < short_window < long_window")

        bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
        if len(bars) < long_window:
            return []

        short_avg = _average_price(bars[-short_window:])
        long_avg = _average_price(bars[-long_window:])
        state = context.portfolio.state(context.portfolio_name, through=context.current_date)
        held = state.holdings.get(symbol)
        held_shares = held.shares if held else 0

        if short_avg > long_avg and held_shares == 0:
            price = _bar_price(bars[-1])
            shares = floor(state.cash / price)
            return [Order(symbol, "buy", shares)] if shares > 0 else []
        if short_avg <= long_avg and held_shares > 0:
            return [Order(symbol, "sell", held_shares)]
        return []


class MomentumStrategy(Strategy):
    name = "momentum"
    description = "Rebalance into the top N symbols by lookback return every N trading days."

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        lookback = int(context.parameters.get("lookback_days", 63))
        top_n = int(context.parameters.get("top_n", 5))
        rebalance_days = int(context.parameters.get("rebalance_days", 21))
        if lookback <= 0 or top_n <= 0 or rebalance_days <= 0:
            raise ValueError("momentum parameters must be positive")

        trading_dates = _trading_dates(
            context.db, context.symbols, context.start, context.current_date
        )
        if not trading_dates:
            return []
        day_index = len(trading_dates) - 1
        if day_index % rebalance_days != 0:
            return []

        ranked: list[tuple[float, str]] = []
        for symbol in context.symbols:
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            if len(bars) <= lookback:
                continue
            old_price = _bar_price(bars[-lookback - 1])
            current_price = _bar_price(bars[-1])
            if old_price > 0:
                ranked.append((current_price / old_price - 1.0, symbol))
        selected = [symbol for _, symbol in sorted(ranked, reverse=True)[:top_n]]
        if not selected:
            return []
        narrowed = StrategyContext(
            db=context.db,
            portfolio=context.portfolio,
            portfolio_name=context.portfolio_name,
            current_date=context.current_date,
            symbols=selected,
            start=context.start,
            end=context.end,
            parameters=context.parameters,
            progress=context.progress,
        )
        return _target_equal_weight_orders(narrowed)


class MeanReversionStrategy(Strategy):
    name = "mean-reversion"
    description = "Rebalance into the worst N symbols by lookback return every N trading days."

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        lookback = int(context.parameters.get("lookback_days", 21))
        top_n = int(context.parameters.get("top_n", 5))
        rebalance_days = int(context.parameters.get("rebalance_days", 21))
        if lookback <= 0 or top_n <= 0 or rebalance_days <= 0:
            raise ValueError("mean-reversion parameters must be positive")
        if not _is_rebalance_date(context, lookback, rebalance_days):
            return []

        ranked: list[tuple[float, str]] = []
        for symbol in context.symbols:
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            if len(bars) <= lookback:
                continue
            old_price = _bar_price(bars[-lookback - 1])
            current_price = _bar_price(bars[-1])
            if old_price > 0:
                ranked.append((current_price / old_price - 1.0, symbol))
        selected = [symbol for _, symbol in sorted(ranked)[:top_n]]
        return _rebalance_to_symbols(context, selected)


class LowVolatilityStrategy(Strategy):
    name = "low-volatility"
    description = "Rebalance into the lowest-volatility N symbols over a lookback window."

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        lookback = int(context.parameters.get("lookback_days", 63))
        top_n = int(context.parameters.get("top_n", 10))
        rebalance_days = int(context.parameters.get("rebalance_days", 21))
        if lookback <= 1 or top_n <= 0 or rebalance_days <= 0:
            raise ValueError(
                "low-volatility requires lookback_days > 1 and positive top_n/rebalance_days"
            )
        if not _is_rebalance_date(context, lookback, rebalance_days):
            return []

        ranked: list[tuple[float, str]] = []
        for symbol in context.symbols:
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            if len(bars) <= lookback:
                continue
            returns = _returns(bars[-lookback - 1 :])
            if returns:
                ranked.append((_stddev(returns), symbol))
        selected = [symbol for _, symbol in sorted(ranked)[:top_n]]
        return _rebalance_to_symbols(context, selected)


class BreakoutStrategy(Strategy):
    name = "breakout"
    description = (
        "Hold symbols breaking above their prior lookback high, rebalanced every N trading days."
    )

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        lookback = int(context.parameters.get("lookback_days", 55))
        top_n = int(context.parameters.get("top_n", 5))
        rebalance_days = int(context.parameters.get("rebalance_days", 5))
        if lookback <= 1 or top_n <= 0 or rebalance_days <= 0:
            raise ValueError(
                "breakout requires lookback_days > 1 and positive top_n/rebalance_days"
            )
        if not _is_rebalance_date(context, lookback, rebalance_days):
            return []

        ranked: list[tuple[float, str]] = []
        for symbol in context.symbols:
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            if len(bars) <= lookback:
                continue
            current_price = _bar_price(bars[-1])
            prior_high = max(_bar_price(bar) for bar in bars[-lookback - 1 : -1])
            if prior_high > 0 and current_price > prior_high:
                ranked.append((current_price / prior_high - 1.0, symbol))
        selected = [symbol for _, symbol in sorted(ranked, reverse=True)[:top_n]]
        return _rebalance_to_symbols(context, selected)


class TrendFollowingStrategy(Strategy):
    name = "trend-following"
    description = "Hold the strongest N symbols that are above their moving average."

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        lookback = int(context.parameters.get("lookback_days", 126))
        top_n = int(context.parameters.get("top_n", 10))
        rebalance_days = int(context.parameters.get("rebalance_days", 21))
        if lookback <= 1 or top_n <= 0 or rebalance_days <= 0:
            raise ValueError(
                "trend-following requires lookback_days > 1 and positive top_n/rebalance_days"
            )
        if not _is_rebalance_date(context, lookback, rebalance_days):
            return []

        ranked: list[tuple[float, str]] = []
        for symbol in context.symbols:
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            if len(bars) <= lookback:
                continue
            current_price = _bar_price(bars[-1])
            moving_average = _average_price(bars[-lookback:])
            old_price = _bar_price(bars[-lookback - 1])
            if old_price > 0 and current_price > moving_average:
                ranked.append((current_price / old_price - 1.0, symbol))
        selected = [symbol for _, symbol in sorted(ranked, reverse=True)[:top_n]]
        return _rebalance_to_symbols(context, selected)


class RelativeStrengthIndexStrategy(Strategy):
    name = "rsi-strength"
    description = "Rebalance into the top N symbols by relative strength index (RSI)."

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        lookback = int(context.parameters.get("lookback_days", 14))
        top_n = int(context.parameters.get("top_n", 10))
        rebalance_days = int(context.parameters.get("rebalance_days", 21))
        if lookback <= 1 or top_n <= 0 or rebalance_days <= 0:
            raise ValueError(
                "rsi-strength requires lookback_days > 1 and positive top_n/rebalance_days"
            )
        if not _is_rebalance_date(context, lookback, rebalance_days):
            return []

        ranked: list[tuple[float, str]] = []
        for symbol in context.symbols:
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            if len(bars) <= lookback:
                continue
            ranked.append((_rsi(bars[-lookback - 1 :]), symbol))
        selected = [symbol for _, symbol in sorted(ranked, reverse=True)[:top_n]]
        return _rebalance_to_symbols(context, selected)


class DualMomentumStrategy(Strategy):
    name = "dual-momentum"
    description = "Ranks trailing momentum with a skipped recent month; holds only positive trends, otherwise cash."

    def generate_orders(self, context):
        lookback = context.parameters.get("lookback_days", 252)
        skip = context.parameters.get("skip_days", 21)
        if not _is_rebalance_date(context, lookback, context.parameters.get("rebalance_days", 21)):
            return []
        ranked = []
        for symbol in context.symbols:
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            if len(bars) <= lookback:
                continue
            score = _bar_price(bars[-skip - 1]) / _bar_price(bars[-lookback - 1]) - 1
            if score > 0 and _bar_price(bars[-1]) > _average_price(bars[-lookback:]):
                ranked.append((score, symbol))
        return _rebalance_to_symbols(
            context,
            [s for _, s in sorted(ranked, reverse=True)[: context.parameters.get("top_n", 3)]],
        )


class InverseVolatilityStrategy(Strategy):
    name = "inverse-volatility"
    description = "Allocates across selected assets in inverse proportion to trailing volatility; unlevered, without a correlation model."

    def generate_orders(self, context):
        lookback = context.parameters.get("lookback_days", 63)
        if not _is_rebalance_date(context, lookback, context.parameters.get("rebalance_days", 21)):
            return []
        scores = {}
        for symbol in context.symbols:
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            if len(bars) <= lookback:
                continue
            if self.name == "time-series-momentum" and _bar_price(bars[-1]) <= _bar_price(
                bars[-lookback - 1]
            ):
                continue
            scores[symbol] = 1 / max(_stddev(_returns(bars[-lookback - 1 :])), 0.001)
        total = sum(scores.values())
        return _weighted_orders(context, {s: v / total for s, v in scores.items()} if total else {})


class TimeSeriesMomentumStrategy(InverseVolatilityStrategy):
    name = "time-series-momentum"
    description = "Holds assets with positive trailing returns, scaled by inverse volatility; long-only adaptation of time-series momentum."


class NeuralPatternStrategy(Strategy):
    name = "nn-pattern"
    description = (
        "Uses pre-trained neural network weights to rank individual stock pattern features."
    )

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        top_n = int(context.parameters.get("top_n", 10))
        rebalance_days = int(context.parameters.get("rebalance_days", 21))
        lookback = int(context.parameters.get("lookback_days", 63))
        if lookback < 63:
            lookback = 63
        if top_n <= 0 or rebalance_days <= 0:
            raise ValueError("nn-pattern requires positive top_n/rebalance_days")
        if not _is_rebalance_date(context, lookback, rebalance_days):
            return []

        selected = _neural_ranked_symbols(
            context, "nn_stock_pattern_v1", context.current_date, top_n
        )
        return _rebalance_to_symbols(context, selected)


class NeuralSectorRotationStrategy(Strategy):
    name = "nn-sector-rotation"
    description = (
        "Uses pre-trained neural network scores to rotate into the strongest sectors and stocks."
    )

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        top_n = int(context.parameters.get("top_n", 10))
        rebalance_days = int(context.parameters.get("rebalance_days", 21))
        lookback = max(63, int(context.parameters.get("lookback_days", 63)))
        sector_count = int(context.parameters.get("sector_count", 3))
        if top_n <= 0 or rebalance_days <= 0 or sector_count <= 0:
            raise ValueError(
                "nn-sector-rotation requires positive top_n/rebalance_days/sector_count"
            )
        if not _is_rebalance_date(context, lookback, rebalance_days):
            return []

        scored = _neural_symbol_scores(context, "nn_sector_rotation_v1", context.current_date)
        by_sector: dict[str, list[tuple[float, str]]] = {}
        for score, symbol in scored:
            sector = getattr(context.db, "sectors", {}).get(symbol, sector_for_symbol(symbol))
            by_sector.setdefault(sector, []).append((score, symbol))
        sector_scores = [
            (sum(score for score, _ in members) / len(members), sector)
            for sector, members in by_sector.items()
        ]
        selected_sectors = {
            sector for _, sector in sorted(sector_scores, reverse=True)[:sector_count]
        }
        selected = [
            symbol
            for score, symbol in sorted(scored, reverse=True)
            if getattr(context.db, "sectors", {}).get(symbol, sector_for_symbol(symbol))
            in selected_sectors
        ][:top_n]
        return _rebalance_to_symbols(context, selected)


class NeuralRiskAdjustedStrategy(Strategy):
    name = "nn-risk-adjusted"
    description = "Ranks pre-trained neural network predictions after penalizing recent volatility."

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        top_n = int(context.parameters.get("top_n", 10))
        rebalance_days = int(context.parameters.get("rebalance_days", 21))
        lookback = max(63, int(context.parameters.get("lookback_days", 63)))
        if top_n <= 0 or rebalance_days <= 0:
            raise ValueError("nn-risk-adjusted requires positive top_n/rebalance_days")
        if not _is_rebalance_date(context, lookback, rebalance_days):
            return []

        ranked: list[tuple[float, str]] = []
        for score, symbol in _neural_symbol_scores(
            context, "nn_stock_pattern_v1", context.current_date
        ):
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            returns = _returns(bars[-22:])
            ranked.append((score - _stddev(returns) * 4.0, symbol))
        selected = [symbol for _, symbol in sorted(ranked, reverse=True)[:top_n]]
        return _rebalance_to_symbols(context, selected)


class AgenticResearchStrategy(Strategy):
    name = "agentic-research"
    description = "Periodically researches one new candidate, stores the decision, syncs limited data, and buys a small discovery basket."

    def __init__(self):
        self._states = {}

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        lookback = max(20, int(context.parameters.get("lookback_days", 63)))
        research_interval = int(
            context.parameters.get(
                "research_interval_days", context.parameters.get("rebalance_days", 63)
            )
        )
        max_researches = int(context.parameters.get("agent_max_researches", 4))
        top_n = int(context.parameters.get("top_n", 5))
        online_research = bool(int(context.parameters.get("agent_online_research", 0)))
        if research_interval <= 0 or max_researches <= 0 or top_n <= 0:
            raise ValueError(
                "agentic-research requires positive research_interval_days, agent_max_researches, and top_n"
            )

        trading_dates = _trading_dates(
            context.db, context.symbols, context.start, context.current_date
        )
        if len(trading_dates) <= lookback:
            return []
        day_index = len(trading_dates) - 1
        state = self._states.setdefault(
            context.portfolio_name,
            {"discoveries": [], "researched": set(), "last_research_index": -1},
        )

        if (
            day_index % research_interval == 0
            and state["last_research_index"] != day_index
            and len(state["researched"]) < max_researches
        ):
            self._research_one_candidate(context, state, lookback, online_research)
            state["last_research_index"] = day_index

        discoveries = [
            symbol
            for symbol in state["discoveries"][-top_n:]
            if context.db.get_bars(symbol, context.current_date, context.current_date)
        ]
        return _rebalance_to_symbols(context, discoveries)

    def _research_one_candidate(
        self,
        context: StrategyContext,
        state: dict,
        lookback: int,
        online_research: bool,
    ) -> None:
        fetched_this_window = False
        for raw_symbol in AGENTIC_RESEARCH_CANDIDATES:
            symbol = normalize_symbol(raw_symbol)
            if symbol in state["researched"] or symbol in context.symbols:
                continue
            context.db.add_symbol(symbol)
            bars = _symbol_bars_ending_on(context.db, symbol, context.start, context.current_date)
            source = "local-market-data"
            if len(bars) <= lookback and online_research and not fetched_this_window:
                source = _sync_agentic_candidate(context, symbol)
                fetched_this_window = True
                bars = _symbol_bars_ending_on(
                    context.db, symbol, context.start, context.current_date
                )
            if len(bars) <= lookback:
                if online_research and fetched_this_window:
                    state["researched"].add(symbol)
                    _record_agentic_decision(
                        context,
                        symbol,
                        "PASS",
                        0.0,
                        "Insufficient OHLCV coverage after one rate-limited fetch.",
                        source,
                    )
                    _emit(
                        context.progress,
                        {
                            "type": "research",
                            "date": context.current_date.isoformat(),
                            "symbol": symbol,
                            "verdict": "PASS",
                            "message": f"{context.current_date} RESEARCH {symbol} pass: insufficient OHLCV coverage",
                        },
                    )
                    return
                continue

            score = _agentic_research_score(bars, lookback)
            rationale = _agentic_rationale(bars, lookback, score)
            state["researched"].add(symbol)
            verdict = "TAKE" if score > 0 else "PASS"
            if verdict == "TAKE":
                state["discoveries"].append(symbol)
            _record_agentic_decision(context, symbol, verdict, score, rationale, source)
            _emit(
                context.progress,
                {
                    "type": "research",
                    "date": context.current_date.isoformat(),
                    "symbol": symbol,
                    "verdict": verdict,
                    "score": score,
                    "message": f"{context.current_date} RESEARCH {symbol} {verdict.lower()}: score {score:.3f}; {rationale}",
                },
            )
            return


class HybridStrategy(Strategy):
    name = "hybrid"
    description = (
        "Switches among existing strategies based on recent reevaluation-window performance."
    )
    candidates = (
        BuyAndHoldStrategy.name,
        EqualWeightStrategy.name,
        MovingAverageCrossoverStrategy.name,
        MomentumStrategy.name,
        MeanReversionStrategy.name,
        LowVolatilityStrategy.name,
        BreakoutStrategy.name,
        TrendFollowingStrategy.name,
        RelativeStrengthIndexStrategy.name,
        NeuralPatternStrategy.name,
        NeuralSectorRotationStrategy.name,
        NeuralRiskAdjustedStrategy.name,
    )

    def __init__(self):
        self._states = {}

    def generate_orders(self, context: StrategyContext) -> list[Order]:
        from .simulation import SimulationBroker

        viable = [
            name
            for name in self.candidates
            if name != "moving-average" or len(context.symbols) == 1
        ]
        state = self._states.get(context.portfolio_name)
        if state is None:
            initial = self._random_initial_strategy(context, viable)
            cash = context.portfolio.ledger.starting_cash
            state = {
                "active": initial,
                "wins": {name: 0 for name in viable},
                "days": 0,
                "shadows": {},
            }
            for name in viable:
                broker = SimulationBroker(context.db, 0, name, cash, context.parameters)
                state["shadows"][name] = {
                    "broker": broker,
                    "strategy": type(STRATEGIES[name])(),
                    "pending": [],
                    "baseline": cash,
                }
            self._states[context.portfolio_name] = state
            _emit(
                context.progress,
                {
                    "type": "strategy_switch",
                    "date": str(context.current_date),
                    "strategy": initial,
                    "message": f"{context.current_date} HYBRID initial strategy {initial}",
                },
            )
        scores = []
        desired = {}
        for name, shadow in state["shadows"].items():
            broker = shadow["broker"]
            broker.execute(shadow["pending"], context.current_date)
            value = broker.value(name, context.current_date).total_value
            child_params = dict(context.parameters)
            if name in {"rsi-strength", "breakout", "low-volatility", "trend-following"}:
                child_params["lookback_days"] = max(2, child_params.get("lookback_days", 63))
            child = replace(
                context,
                portfolio=broker,
                portfolio_name=name,
                parameters=child_params,
                progress=None,
            )
            shadow["pending"] = shadow["strategy"].generate_orders(child)
            raw = value / shadow["baseline"] - 1 if shadow["baseline"] else 0
            weight = state["wins"][name] / max(1, sum(state["wins"].values()))
            scores.append((name, raw, raw + 0.005 * weight))
            quantities = {s: p.shares for s, p in broker.ledger.holdings.items()}
            for order in shadow["pending"]:
                quantities[order.symbol] = max(
                    0,
                    quantities.get(order.symbol, 0)
                    + order.shares * (1 if order.side == "buy" else -1),
                )
            allocation = {
                s: q * _bar_price(bar) / value
                for s, q in quantities.items()
                if q > 0
                and value > 0
                and (bar := context.db.get_bar_on_or_before(s, context.current_date))
            }
            scale = max(1.0, sum(allocation.values()))
            desired[name] = {s: w / scale for s, w in allocation.items()}
        if state["days"] > 0 and state["days"] % context.parameters.get("rebalance_days", 21) == 0:
            winner = max(scores, key=lambda x: x[1])[0]
            state["wins"][winner] += 1
            selected, raw, weighted = max(scores, key=lambda x: x[2])
            previous = state["active"]
            if selected != previous:
                state["active"] = selected
                _emit(
                    context.progress,
                    {
                        "type": "strategy_switch",
                        "date": str(context.current_date),
                        "from_strategy": previous,
                        "to_strategy": selected,
                        "wins": dict(state["wins"]),
                        "window_return": raw,
                        "weighted_score": weighted,
                        "message": f"{context.current_date} HYBRID switch {previous} -> {selected} (net window {raw:.2%})",
                    },
                )
            for name, shadow in state["shadows"].items():
                shadow["baseline"] = shadow["broker"].value(name, context.current_date).total_value
        state["days"] += 1
        # Shadow allocations already include reserve cash and concentration limits.
        return _weighted_orders(
            replace(context, parameters={**context.parameters, "cash_reserve_pct": 0}),
            desired[state["active"]],
        )

    def _random_initial_strategy(self, context: StrategyContext, viable: list[str]) -> str:
        candidates = [
            strategy_name
            for strategy_name in viable
            if _hybrid_selected_symbols(strategy_name, context, context.current_date)
        ]
        return Random(context.parameters.get("seed", 42)).choice(candidates or viable)


STRATEGIES: dict[str, Strategy] = {
    strategy.name: strategy
    for strategy in (
        BuyAndHoldStrategy(),
        EqualWeightStrategy(),
        MovingAverageCrossoverStrategy(),
        MomentumStrategy(),
        MeanReversionStrategy(),
        LowVolatilityStrategy(),
        BreakoutStrategy(),
        TrendFollowingStrategy(),
        RelativeStrengthIndexStrategy(),
        DualMomentumStrategy(),
        InverseVolatilityStrategy(),
        TimeSeriesMomentumStrategy(),
        NeuralPatternStrategy(),
        NeuralSectorRotationStrategy(),
        NeuralRiskAdjustedStrategy(),
        AgenticResearchStrategy(),
        HybridStrategy(),
    )
}


class BacktestService:
    def __init__(self, db: Database):
        self.db = db
        self.portfolios = PortfolioService(db)

    def strategies(self) -> dict[str, str]:
        return {name: strategy.description for name, strategy in STRATEGIES.items()}

    def run(
        self,
        strategy_name: str,
        symbols: list[str],
        start: date,
        end: date,
        starting_cash: float,
        parameters: dict | None = None,
        benchmark_symbol: str = "SPY",
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        from .engine import run_backtest

        return run_backtest(
            self,
            strategy_name,
            symbols,
            start,
            end,
            starting_cash,
            parameters,
            benchmark_symbol,
            progress,
            cancelled,
        )

    def catalog(self):
        return [
            {
                "id": key,
                "name": value[0],
                "category": value[1],
                "defaults": value[2],
                "description": STRATEGIES[key].description,
            }
            for key, value in CATALOG.items()
        ]

    def _resolve_symbols(self, symbols: list[str]) -> list[str]:
        normalized = [normalize_symbol(symbol) for symbol in symbols]
        if len(normalized) == 1 and normalized[0] in UNIVERSE_KEYWORDS:
            return [row["symbol"] for row in self.db.list_symbols()]
        if len(normalized) == 1 and re.fullmatch(r"@TOP[0-9]+", normalized[0]):
            from .market_caps import MarketCapService

            return [r["symbol"] for r in MarketCapService(self.db).ranked(int(normalized[0][4:]))]
        if any(symbol in UNIVERSE_KEYWORDS or symbol.startswith("@") for symbol in normalized):
            raise ValueError("use @universe or @topN by itself, with N a positive integer")
        return list(dict.fromkeys(self.db.require_symbol(symbol) for symbol in normalized))


def _target_equal_weight_orders(context: StrategyContext) -> list[Order]:
    available = [
        s
        for s in context.symbols
        if context.db.get_bars(s, context.current_date, context.current_date)
    ]
    return (
        _weighted_orders(context, {s: 1 / len(available) for s in available}) if available else []
    )


def _weighted_orders(context: StrategyContext, weights: dict[str, float]) -> list[Order]:
    state = context.portfolio.state(context.portfolio_name, through=context.current_date)
    prices = {
        symbol: _bar_price(bars[-1])
        for symbol in weights
        if (bars := context.db.get_bars(symbol, context.current_date, context.current_date))
    }

    holding_prices = {
        symbol: _bar_price(bar)
        for symbol in state.holdings
        if (bar := context.db.get_bar_on_or_before(symbol, context.current_date)) is not None
    }
    total_value = state.cash + sum(
        position.shares * holding_prices.get(symbol, 0.0)
        for symbol, position in state.holdings.items()
    )
    investable = total_value * (1 - context.parameters.get("cash_reserve_pct", 0) / 100)
    cap = total_value * context.parameters.get("max_position_pct", 100) / 100
    tolerance = total_value * context.parameters.get("rebalance_tolerance_pct", 0) / 100
    targets = {s: floor(min(investable * weights[s], cap) / p) for s, p in prices.items()}
    orders: list[Order] = []

    for symbol, position in sorted(state.holdings.items()):
        if symbol not in prices:
            orders.append(Order(symbol, "sell", position.shares))

    for symbol, price in sorted(prices.items()):
        current_shares = state.holdings.get(symbol).shares if symbol in state.holdings else 0
        target_shares = targets[symbol]
        delta = target_shares - current_shares
        if delta < 0 and abs(delta) * price > tolerance:
            orders.append(Order(symbol, "sell", abs(delta)))

    simulated_cash = state.cash
    simulated_holdings = {symbol: position.shares for symbol, position in state.holdings.items()}
    for order in orders:
        price = holding_prices.get(order.symbol) or prices.get(order.symbol)
        if price is None:
            continue
        if order.side == "sell":
            simulated_cash += price * order.shares
            simulated_holdings[order.symbol] = (
                simulated_holdings.get(order.symbol, 0) - order.shares
            )

    for symbol, price in sorted(prices.items()):
        current_shares = simulated_holdings.get(symbol, 0)
        target_shares = targets[symbol]
        shares_to_buy = max(0, target_shares - current_shares)
        if shares_to_buy * price <= tolerance:
            continue
        affordable = floor(simulated_cash / price)
        shares = min(shares_to_buy, affordable)
        if shares > 0:
            orders.append(Order(symbol, "buy", shares))
            simulated_cash -= price * shares
    return orders


def _rebalance_to_symbols(context: StrategyContext, symbols: list[str]) -> list[Order]:
    if not symbols:
        state = context.portfolio.state(context.portfolio_name, through=context.current_date)
        return [
            Order(symbol, "sell", position.shares)
            for symbol, position in sorted(state.holdings.items())
            if position.shares > 0
        ]
    narrowed = StrategyContext(
        db=context.db,
        portfolio=context.portfolio,
        portfolio_name=context.portfolio_name,
        current_date=context.current_date,
        symbols=symbols,
        start=context.start,
        end=context.end,
        parameters=context.parameters,
        progress=context.progress,
    )
    return _target_equal_weight_orders(narrowed)


def _is_rebalance_date(context: StrategyContext, warmup_days: int, rebalance_days: int) -> bool:
    trading_dates = _trading_dates(context.db, context.symbols, context.start, context.current_date)
    if not trading_dates:
        return False
    day_index = len(trading_dates) - 1
    return day_index % rebalance_days == 0


def _trading_dates(db: Database, symbols: list[str], start: date, end: date) -> list[date]:
    return db.trading_dates(start, end, symbols=symbols)


def _symbol_bars_ending_on(
    db: Database, symbol: str, start: date, current: date
) -> list[MarketBar]:
    if hasattr(db, "contiguous_bars"):
        return db.contiguous_bars(symbol, current)
    bars = db.get_bars(symbol, date.min, current)
    if not bars or bars[-1].trading_date != current:
        return []
    return bars


def _sync_agentic_candidate(context: StrategyContext, symbol: str) -> str:
    row = context.db.get_symbol(symbol)
    provider_symbol = row["provider_symbol"] if row else None
    provider = YahooChartMarketDataProvider()
    _emit(
        context.progress,
        {
            "type": "research_sync",
            "date": context.current_date.isoformat(),
            "symbol": symbol,
            "message": f"{context.current_date} RESEARCH syncing {symbol} from Yahoo",
        },
    )
    result = provider.fetch_daily(
        symbol, context.start, context.end, provider_symbol=provider_symbol
    )
    context.db.record_fetch(result)
    if result.success:
        inserted = context.db.insert_market_bars(result.bars, fetched_at=result.fetched_at)
        if hasattr(context.db, "reload"):
            context.db.reload(symbol)
        _emit(
            context.progress,
            {
                "type": "research_sync",
                "date": context.current_date.isoformat(),
                "symbol": symbol,
                "rows": inserted,
                "message": f"{context.current_date} RESEARCH synced {symbol}: {inserted} rows",
            },
        )
        return provider.name
    _emit(
        context.progress,
        {
            "type": "research_sync",
            "date": context.current_date.isoformat(),
            "symbol": symbol,
            "message": f"{context.current_date} RESEARCH {symbol} fetch failed: {result.error_message}",
        },
    )
    return provider.name


def _record_agentic_decision(
    context: StrategyContext,
    symbol: str,
    verdict: str,
    score: float,
    rationale: str,
    source: str,
) -> None:
    portfolio = context.db.get_portfolio(context.portfolio_name)
    if portfolio is None:
        return
    context.db.insert_research_decision(
        portfolio_id=portfolio["id"],
        strategy_name=AgenticResearchStrategy.name,
        symbol=symbol,
        research_date=context.current_date,
        verdict=verdict,
        score=score,
        rationale=rationale,
        source=source,
    )


def _agentic_research_score(bars: list[MarketBar], lookback: int) -> float:
    window = bars[-lookback - 1 :]
    prices = [_bar_price(bar) for bar in window]
    start_price = prices[0]
    end_price = prices[-1]
    if start_price <= 0:
        return 0.0
    momentum = end_price / start_price - 1.0
    volatility = _stddev(_returns(window))
    volume_trend = _volume_trend(window)
    rsi_balance = 1.0 - abs(_rsi(window) - 55.0) / 55.0
    return momentum * 1.4 - volatility * 3.0 + volume_trend * 0.25 + rsi_balance * 0.05


def _agentic_rationale(bars: list[MarketBar], lookback: int, score: float) -> str:
    window = bars[-lookback - 1 :]
    start_price = _bar_price(window[0])
    end_price = _bar_price(window[-1])
    momentum = end_price / start_price - 1.0 if start_price > 0 else 0.0
    return (
        f"{lookback}-day return {momentum:.2%}, RSI {_rsi(window):.1f}, "
        f"volume trend {_volume_trend(window):.2f}, composite {score:.3f}"
    )


def _volume_trend(bars: list[MarketBar]) -> float:
    midpoint = max(1, len(bars) // 2)
    early = [bar.volume for bar in bars[:midpoint]]
    late = [bar.volume for bar in bars[midpoint:]]
    early_average = sum(early) / len(early)
    late_average = sum(late) / len(late) if late else early_average
    if early_average <= 0:
        return 0.0
    return late_average / early_average - 1.0


def _strategy_model_names(strategy_name: str) -> list[str]:
    if strategy_name == NeuralPatternStrategy.name:
        return ["nn_stock_pattern_v1"]
    if strategy_name == NeuralSectorRotationStrategy.name:
        return ["nn_sector_rotation_v1"]
    if strategy_name == NeuralRiskAdjustedStrategy.name:
        return ["nn_stock_pattern_v1"]
    if strategy_name == HybridStrategy.name:
        return ["nn_stock_pattern_v1", "nn_sector_rotation_v1"]
    return []


def _neural_symbol_scores(
    context: StrategyContext, model_name: str, as_of: date
) -> list[tuple[float, str]]:
    ranked: list[tuple[float, str]] = []
    for symbol in context.symbols:
        bars = _symbol_bars_ending_on(context.db, symbol, context.start, as_of)
        score = neural_score(model_name, bars, as_of, getattr(context.db, "neural_registry", None))
        if score is not None:
            ranked.append((score, symbol))
    return ranked


def _neural_ranked_symbols(
    context: StrategyContext,
    model_name: str,
    as_of: date,
    top_n: int,
) -> list[str]:
    return [
        symbol
        for _, symbol in sorted(_neural_symbol_scores(context, model_name, as_of), reverse=True)[
            :top_n
        ]
    ]


def _hybrid_selected_symbols(
    strategy_name: str, context: StrategyContext, as_of: date
) -> list[str]:
    lookback = int(context.parameters.get("lookback_days", 63))
    top_n = int(context.parameters.get("top_n", 5))
    if top_n <= 0:
        return []
    if strategy_name in (BuyAndHoldStrategy.name, EqualWeightStrategy.name):
        return [symbol for symbol in context.symbols if context.db.get_bars(symbol, as_of, as_of)]
    if strategy_name == MovingAverageCrossoverStrategy.name:
        if len(context.symbols) != 1:
            return []
        short_window = int(context.parameters.get("short_window", 20))
        long_window = int(context.parameters.get("long_window", 50))
        if short_window <= 0 or long_window <= 0 or short_window >= long_window:
            return []
        bars = _symbol_bars_ending_on(context.db, context.symbols[0], context.start, as_of)
        if len(bars) < long_window:
            return []
        return (
            [context.symbols[0]]
            if _average_price(bars[-short_window:]) > _average_price(bars[-long_window:])
            else []
        )
    if lookback <= 0:
        return []

    ranked: list[tuple[float, str]] = []
    for symbol in context.symbols:
        bars = _symbol_bars_ending_on(context.db, symbol, context.start, as_of)
        if len(bars) <= lookback:
            continue
        old_price = _bar_price(bars[-lookback - 1])
        current_price = _bar_price(bars[-1])
        if old_price <= 0:
            continue
        if strategy_name == MomentumStrategy.name:
            ranked.append((current_price / old_price - 1.0, symbol))
        elif strategy_name == MeanReversionStrategy.name:
            ranked.append((current_price / old_price - 1.0, symbol))
        elif strategy_name == LowVolatilityStrategy.name:
            returns = _returns(bars[-lookback - 1 :])
            if returns:
                ranked.append((_stddev(returns), symbol))
        elif strategy_name == BreakoutStrategy.name:
            prior_high = max(_bar_price(bar) for bar in bars[-lookback - 1 : -1])
            if prior_high > 0 and current_price > prior_high:
                ranked.append((current_price / prior_high - 1.0, symbol))
        elif strategy_name == TrendFollowingStrategy.name:
            moving_average = _average_price(bars[-lookback:])
            if current_price > moving_average:
                ranked.append((current_price / old_price - 1.0, symbol))
        elif strategy_name == RelativeStrengthIndexStrategy.name:
            ranked.append((_rsi(bars[-lookback - 1 :]), symbol))
        elif strategy_name in (NeuralPatternStrategy.name, NeuralRiskAdjustedStrategy.name):
            score = neural_score(
                "nn_stock_pattern_v1", bars, as_of, getattr(context.db, "neural_registry", None)
            )
            if score is not None:
                if strategy_name == NeuralRiskAdjustedStrategy.name:
                    score -= _stddev(_returns(bars[-22:])) * 4.0
                ranked.append((score, symbol))
        elif strategy_name == NeuralSectorRotationStrategy.name:
            score = neural_score(
                "nn_sector_rotation_v1", bars, as_of, getattr(context.db, "neural_registry", None)
            )
            if score is not None:
                ranked.append((score, symbol))

    if strategy_name in (MeanReversionStrategy.name, LowVolatilityStrategy.name):
        return [symbol for _, symbol in sorted(ranked)[:top_n]]
    return [symbol for _, symbol in sorted(ranked, reverse=True)[:top_n]]


def _bar_price(bar: MarketBar) -> float:
    return bar.adjusted_close if bar.adjusted_close is not None else bar.close


def _average_price(bars: list[MarketBar]) -> float:
    return sum(_bar_price(bar) for bar in bars) / len(bars)


def _returns(bars: list[MarketBar]) -> list[float]:
    values = [_bar_price(bar) for bar in bars]
    return [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] != 0
    ]


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return variance**0.5


def _rsi(bars: list[MarketBar]) -> float:
    changes = [
        _bar_price(bars[index]) - _bar_price(bars[index - 1]) for index in range(1, len(bars))
    ]
    if not changes:
        return 50.0
    average_gain = sum(max(change, 0.0) for change in changes) / len(changes)
    average_loss = sum(max(-change, 0.0) for change in changes) / len(changes)
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + average_gain / average_loss))


def _emit(progress: ProgressCallback | None, event: dict) -> None:
    if progress is not None:
        progress(event)
