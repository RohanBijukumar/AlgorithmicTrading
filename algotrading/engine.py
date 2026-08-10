"""Daily next-open backtesting, persisted reports, and reproducible execution metadata."""

from __future__ import annotations

import uuid
from math import isfinite
from time import perf_counter

from .analytics import monthly_returns, performance_metrics
from .market_window import MarketWindow
from .simulation import BacktestCancelled, SimulationBroker, adjusted_price
from .strategy_config import validate_parameters

ENGINE_VERSION = "2.0-next-open"


def preflight(service, strategy_name, symbols, start, end, starting_cash=100000, parameters=None):
    from .backtest import STRATEGIES

    if strategy_name not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy_name}")
    if start >= end:
        raise ValueError("start date must be before end date")
    if not isfinite(starting_cash) or starting_cash <= 0:
        raise ValueError("starting cash must be positive and finite")
    params = validate_parameters(strategy_name, parameters)
    selected = service._resolve_symbols(symbols)
    if not selected:
        raise ValueError("select at least one active symbol")
    if strategy_name == "moving-average" and len(selected) != 1:
        raise ValueError("moving-average requires exactly one symbol")
    coverage = {r["symbol"]: r for r in service.db.coverage_report()}
    with service.db.connect() as conn:
        counts = {
            r["symbol"]: dict(r)
            for r in conn.execute(
                """SELECT symbol, SUM(trading_date < ?) AS warmup_sessions,
            SUM(trading_date BETWEEN ? AND ?) AS test_sessions
            FROM market_bars WHERE provider='yahoo' AND interval='1d' AND trading_date<=?
            GROUP BY symbol""",
                (str(start), str(start), str(end), str(end)),
            )
        }
    available = [s for s in selected if counts.get(s, {}).get("test_sessions", 0) > 0]
    missing = [s for s in selected if s not in available]
    warnings = []
    lookback = params.get("lookback_days", params.get("long_window", 0))
    warmup_missing = [s for s in available if counts[s]["warmup_sessions"] < lookback]
    if warmup_missing:
        warnings.append(
            f"{len(warmup_missing)} symbols lack {lookback} pre-start warm-up sessions; their signals will wait for sufficient contiguous history."
        )
    if missing:
        warnings.append(
            f"{len(missing)} selected symbols have no data in this window and will be excluded."
        )
    if any(str(s).upper().startswith("@TOP") for s in symbols):
        warnings.append(
            "Top selectors use today's Nasdaq USD market caps among active companies, not point-in-time historical constituents. Missing caps are excluded."
        )
    warnings.append(
        "The current symbol universe has survivorship bias; historical membership and delisting proceeds are not modeled."
    )
    warnings.append(
        "Adjusted-price simulation: share counts are normalized units. Dividends and splits are embedded in prices, not posted as cash or share events."
    )
    if strategy_name.startswith("nn-") or strategy_name == "hybrid":
        from .walk_forward import audit_registry

        audit = audit_registry()
        missing_years = sorted(set(range(start.year, end.year + 1)) - set(audit["fold_years"]))
        warnings.append(
            "Neural models use purged, pre-year walk-forward ensembles. Repeated research and surviving-universe bias still limit performance claims."
        )
        if missing_years:
            warnings.append(
                f"No eligible neural folds for {missing_years}; neural signals stay in cash in those years. Train older/newer folds offline with 'model train'."
            )
    if strategy_name == "agentic-research":
        warnings.append(
            "Discovery is an OHLCV screen on a curated candidate list, not an LLM or historical news researcher. Online mode only fetches Yahoo bars."
        )
    return {
        "ready": bool(available),
        "requested_symbols": selected,
        "symbols": available,
        "missing_symbols": missing,
        "warmup_missing": warmup_missing,
        "warnings": warnings,
        "parameters": params,
        "coverage": [{**coverage[s], **counts.get(s, {})} for s in selected if s in coverage],
    }


def run_backtest(
    service,
    strategy_name,
    symbols,
    start,
    end,
    starting_cash,
    parameters,
    benchmark_symbol,
    progress,
    cancelled,
):
    from .backtest import STRATEGIES, BacktestResult, StrategyContext, _strategy_model_names

    service.db.initialize()
    check = preflight(service, strategy_name, symbols, start, end, starting_cash, parameters)
    if not check["ready"]:
        raise LookupError(
            "no selected symbols have market data in the requested window; sync data first"
        )
    params = check["parameters"]
    selected = check["symbols"]
    benchmark_symbol = benchmark_symbol.strip().upper()
    clock_start = perf_counter()
    window = MarketWindow(service.db, list(dict.fromkeys(selected + [benchmark_symbol])), end)
    if _strategy_model_names(strategy_name):
        from .walk_forward import audit_registry, load_registry

        window.neural_registry = load_registry() or {}
        window.neural_audit = audit_registry(window.neural_registry)
    dates = window.trading_dates(start, end, symbols=selected)
    if len(dates) < 2:
        raise LookupError(
            "at least two stored trading sessions are required for next-open execution"
        )
    name = f"backtest-{strategy_name}-{uuid.uuid4().hex[:10]}"
    portfolio_id = service.portfolios.create(name, starting_cash)
    broker = SimulationBroker(window, portfolio_id, name, starting_cash, params)
    strategy = type(STRATEGIES[strategy_name])()
    emit = progress or (lambda event: None)
    pending = []
    points = []
    stale = set()
    warnings = list(check["warnings"])
    previous = starting_cash
    benchmark_entry = window.get_bars(benchmark_symbol, dates[1], dates[1])
    benchmark_price = (
        benchmark_entry[0].open * adjusted_price(benchmark_entry[0]) / benchmark_entry[0].close
        if benchmark_entry
        else None
    )
    benchmark_points = []
    try:
        emit(
            {
                "type": "started",
                "message": f"Running {strategy_name} across {len(selected)} symbols; next-session open, ${params['commission']:.2f} commission, {params['slippage_bps']:g} bps slippage",
            }
        )
        for index, current in enumerate(dates):
            if cancelled and cancelled():
                raise BacktestCancelled("Backtest cancelled")
            window.frontier = current
            if _strategy_model_names(strategy_name) and (
                index == 0 or current.year != dates[index - 1].year
            ):
                from .walk_forward import fold_for_date

                fold = fold_for_date(current, window.neural_registry)
                message = (
                    (
                        f"Neural fold {current.year}: {len(fold['members'])} pre-year models; validation outcomes end "
                        + max(m["last_validation_label_date"] for m in fold["members"])
                    )
                    if fold
                    else f"No neural fold for {current.year}; no neural selections until an eligible fold"
                )
                emit({"type": "model_fold", "date": str(current), "message": message})
            broker.execute(pending, current, emit)
            valuation = broker.value(name, current)
            for symbol in broker.ledger.holdings:
                if not window.get_bars(symbol, current, current):
                    stale.add(symbol)
            point = {
                "date": str(current),
                "cash": broker.ledger.cash,
                "market_value": valuation.market_value,
                "total_value": valuation.total_value,
                "realized_pnl": broker.ledger.realized_pnl,
                "unrealized_pnl": valuation.unrealized_pnl,
                "holding_values": valuation.holding_values,
            }
            points.append(point)
            benchmark_bar = window.get_bars(benchmark_symbol, current, current)
            bvalue = (
                starting_cash
                if index == 0
                else (
                    starting_cash * adjusted_price(benchmark_bar[0]) / benchmark_price
                    if benchmark_bar and benchmark_price
                    else None
                )
            )
            benchmark_points.append({"date": str(current), "total_value": bvalue})
            daily = {
                **point,
                "type": "daily_return",
                "total_return": valuation.total_value / starting_cash - 1,
                "day_return": valuation.total_value / previous - 1 if previous else 0,
                "completed_days": index + 1,
                "total_days": len(dates),
                "trades_executed": len(broker.trades),
                "benchmark_return": bvalue / starting_cash - 1 if bvalue is not None else None,
            }
            daily["message"] = (
                f"{current} RETURN day {daily['day_return']:.2%} total {daily['total_return']:.2%} value ${valuation.total_value:,.2f}"
            )
            emit(daily)
            previous = valuation.total_value
            context = StrategyContext(
                window, broker, name, current, selected, start, end, params, progress
            )
            pending = strategy.generate_orders(context) if index < len(dates) - 1 else []
        if cancelled and cancelled():
            raise BacktestCancelled("Backtest cancelled")
        if stale:
            warnings.append(
                f"Held symbols marked at previous available prices on missing sessions: {', '.join(sorted(stale))}. These marks may understate losses or drawdowns."
            )
        if len(broker.trades) == 0:
            warnings.append(
                "No fills. Check indicator warm-up, available cash, position caps, and strategy filters."
            )
        if any(p["total_value"] is None for p in benchmark_points):
            warnings.append(
                "Benchmark has missing sessions or no entry-session open. Benchmark return is unavailable."
            )
            benchmark_return = None
        else:
            benchmark_return = benchmark_points[-1]["total_value"] / starting_cash - 1
        metrics = performance_metrics(points, starting_cash, params["risk_free_rate"])
        metrics.update(
            {
                "fees": broker.ledger.fees,
                "slippage": broker.ledger.slippage,
                "cash_pct": broker.ledger.cash / metrics["end_value"]
                if metrics["end_value"]
                else 0,
                "average_exposure": sum(
                    p["market_value"] / p["total_value"] if p["total_value"] else 0 for p in points
                )
                / len(points),
                "win_rate": sum(p > 0 for p in broker.ledger.sell_pnls)
                / len(broker.ledger.sell_pnls)
                if broker.ledger.sell_pnls
                else None,
                "excess_return": metrics["total_return"] - benchmark_return
                if benchmark_return is not None
                else None,
            }
        )
        attribution = []
        for symbol in sorted({t.symbol for t in broker.trades}):
            position = broker.ledger.holdings.get(symbol)
            market_value = valuation.holding_values.get(symbol, 0)
            unrealized = market_value - position.shares * position.average_cost if position else 0
            realized = broker.ledger.realized_by_symbol.get(symbol, 0)
            attribution.append(
                {
                    "symbol": symbol,
                    "realized_pnl": realized,
                    "unrealized_pnl": unrealized,
                    "total_pnl": realized + unrealized,
                    "contribution": (realized + unrealized) / starting_cash,
                    "market_value": market_value,
                }
            )
        summary = {
            **metrics,
            "portfolio_name": name,
            "trades_executed": len(broker.trades),
            "symbols": selected,
            "requested_symbols": check["requested_symbols"],
            "missing_symbols": check["missing_symbols"],
            "benchmark_symbol": benchmark_symbol,
            "benchmark_total_return": benchmark_return,
            "benchmark_points": benchmark_points,
            "warnings": warnings,
            "engine_version": ENGINE_VERSION,
            "execution": "Next session adjusted open; long-only, whole normalized units; no leverage or cash interest",
            "monthly_returns": monthly_returns(points, starting_cash),
            "attribution": sorted(attribution, key=lambda r: r["total_pnl"], reverse=True),
            "rejected_orders": len(broker.rejections),
            "elapsed_seconds": perf_counter() - clock_start,
            "accounting_reconciled": True,
            "actual_start": str(dates[0]),
            "actual_end": str(dates[-1]),
        }
        if _strategy_model_names(strategy_name):
            summary["model_artifacts"] = [window.neural_audit]
        if any(str(s).upper().startswith("@TOP") for s in symbols):
            from .market_caps import MarketCapService

            summary["market_cap_snapshot"] = MarketCapService(service.db).status()
        with service.db.transaction():
            broker.persist(service.db, points)
            run_id = service.db.insert_backtest_run(
                strategy_name,
                start,
                end,
                {
                    **params,
                    "symbols": selected,
                    "starting_cash": starting_cash,
                    "benchmark_symbol": benchmark_symbol,
                },
                summary,
            )
        emit({"type": "completed", "run_id": run_id, "message": f"Backtest {run_id} complete"})
        return BacktestResult(
            run_id,
            name,
            strategy_name,
            start,
            end,
            len(broker.trades),
            metrics,
            {"symbol": benchmark_symbol, "total_return": benchmark_return},
        )
    except BaseException:
        service.db.delete_portfolio(name)
        raise
