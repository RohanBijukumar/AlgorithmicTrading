from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .backtest import BacktestService
from .config import DEFAULT_DB_PATH
from .db import Database
from .market import MarketService
from .portfolio import PortfolioService
from .providers import YahooChartMarketDataProvider
from .reporting import backtest_report, export_csv
from .strategy_config import PARAMETERS
from .web import run_server


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return

    db = Database(args.db)
    market = MarketService(db, YahooChartMarketDataProvider())
    portfolios = PortfolioService(db)
    backtests = BacktestService(db)

    try:
        args.handler(args, market, portfolios, backtests)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="algotrading")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Default: {DEFAULT_DB_PATH}",
    )
    subparsers = parser.add_subparsers(dest="resource")

    market = subparsers.add_parser("market", help="market data commands")
    market_sub = market.add_subparsers(dest="command")
    _market_commands(market_sub)

    portfolio = subparsers.add_parser("portfolio", help="portfolio commands")
    portfolio_sub = portfolio.add_subparsers(dest="command")
    _portfolio_commands(portfolio_sub)

    backtest = subparsers.add_parser("backtest", help="strategy backtesting commands")
    backtest_sub = backtest.add_subparsers(dest="command")
    _backtest_commands(backtest_sub)

    ui = subparsers.add_parser("ui", help="local web interface")
    ui_sub = ui.add_subparsers(dest="command")
    _ui_commands(ui_sub)
    model = subparsers.add_parser("model", help="neural artifact inspection")
    model_sub = model.add_subparsers(dest="command")
    audit = model_sub.add_parser(
        "audit", help="inspect checksums, dimensions, and missing training provenance"
    )
    audit.set_defaults(handler=_handle_model_audit)
    train = model_sub.add_parser("train", help="offline purged walk-forward neural training")
    train.add_argument("--from", dest="start", type=_date, default=date(1990, 1, 1))
    train.add_argument("--to", dest="end", type=_date, default=date.today())
    train.add_argument("--first-test-year", type=int, default=1996)
    train.add_argument("--last-test-year", type=int, default=date.today().year)
    train.add_argument("--epochs", type=int, default=40)
    train.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "models" / "walk_forward_v2.json"
    )
    train.set_defaults(handler=_handle_model_train)
    return parser


def _market_commands(subparsers: argparse._SubParsersAction) -> None:
    init = subparsers.add_parser("init", help="initialize local storage")
    init.set_defaults(handler=_handle_market_init)

    universe = subparsers.add_parser("universe", help="list supported symbols")
    universe.set_defaults(handler=_handle_market_universe)
    coverage = subparsers.add_parser("coverage", help="show local data readiness")
    coverage.set_defaults(handler=_handle_market_coverage)
    caps = subparsers.add_parser("cap-refresh", help="refresh current Nasdaq market-cap rankings")
    caps.set_defaults(handler=_handle_cap_refresh)
    expand = subparsers.add_parser("expand", help="add a broad market-cap-ranked company universe")
    expand.add_argument("--count", type=int, default=1500)
    expand.add_argument(
        "--seed-output",
        type=Path,
        help="also export the resolved symbol membership as a reusable JSON seed",
    )
    expand.set_defaults(handler=_handle_market_expand)

    add_symbol = subparsers.add_parser("add-symbol", help="add or update a tradeable symbol")
    add_symbol.add_argument("symbol")
    add_symbol.add_argument("--provider-symbol", help="provider-specific symbol override")
    add_symbol.add_argument("--name", dest="asset_name", help="optional asset name")
    add_symbol.add_argument("--asset-type", default="equity")
    add_symbol.set_defaults(handler=_handle_market_add_symbol)

    remove_symbol = subparsers.add_parser("remove-symbol", help="deactivate a tradeable symbol")
    remove_symbol.add_argument("symbol")
    remove_symbol.set_defaults(handler=_handle_market_remove_symbol)

    sync = subparsers.add_parser("sync", help="fetch and cache historical market data")
    symbol_group = sync.add_mutually_exclusive_group(required=True)
    symbol_group.add_argument("--symbols", nargs="+", help="symbols to sync")
    symbol_group.add_argument("--all", action="store_true", help="sync the full tradeable universe")
    sync.add_argument("--from", dest="start", type=_date, required=True)
    sync.add_argument("--to", dest="end", type=_date, default=date.today())
    sync.add_argument("--force", action="store_true", help="fetch even if local coverage exists")
    sync.add_argument("--fail-fast", action="store_true", help="stop on the first provider failure")
    sync.set_defaults(handler=_handle_market_sync)

    refresh = subparsers.add_parser("refresh", help="refresh a recent trailing window")
    refresh.add_argument("--symbols", nargs="+", required=True)
    refresh.add_argument("--days", type=int, default=10)
    refresh.set_defaults(handler=_handle_market_refresh)

    show = subparsers.add_parser("show", help="show stored market bars")
    show.add_argument("symbol")
    show.add_argument("--from", dest="start", type=_date, required=True)
    show.add_argument("--to", dest="end", type=_date, required=True)
    show.set_defaults(handler=_handle_market_show)

    price = subparsers.add_parser("price", help="show the stored price on or before a date")
    price.add_argument("symbol")
    price.add_argument("--date", dest="target_date", type=_date, required=True)
    price.add_argument(
        "--exact",
        action="store_true",
        help="require an exact trading-date match instead of using the previous bar",
    )
    price.set_defaults(handler=_handle_market_price)

    replay = subparsers.add_parser("replay", help="replay stored market dates")
    replay.add_argument("--from", dest="start", type=_date, required=True)
    replay.add_argument("--to", dest="end", type=_date, required=True)
    replay.set_defaults(handler=_handle_market_replay)


def _portfolio_commands(subparsers: argparse._SubParsersAction) -> None:
    create = subparsers.add_parser("create", help="create a portfolio")
    create.add_argument("--name", required=True)
    create.add_argument("--cash", type=float, required=True)
    create.set_defaults(handler=_handle_portfolio_create)

    buy = subparsers.add_parser("buy", help="buy shares")
    buy.add_argument("portfolio")
    buy.add_argument("symbol")
    buy.add_argument("--shares", type=int, required=True)
    buy.add_argument("--date", dest="execution_date", type=_date, required=True)
    buy.set_defaults(handler=_handle_portfolio_buy)

    sell = subparsers.add_parser("sell", help="sell shares")
    sell.add_argument("portfolio")
    sell.add_argument("symbol")
    sell.add_argument("--shares", type=int, required=True)
    sell.add_argument("--date", dest="execution_date", type=_date, required=True)
    sell.set_defaults(handler=_handle_portfolio_sell)

    holdings = subparsers.add_parser("holdings", help="show holdings")
    holdings.add_argument("portfolio")
    holdings.add_argument("--date", dest="valuation_date", type=_date)
    holdings.set_defaults(handler=_handle_portfolio_holdings)

    cash = subparsers.add_parser("cash", help="show cash balance")
    cash.add_argument("portfolio")
    cash.add_argument("--date", dest="valuation_date", type=_date)
    cash.set_defaults(handler=_handle_portfolio_cash)

    value = subparsers.add_parser("value", help="show portfolio value")
    value.add_argument("portfolio")
    value.add_argument("--date", dest="valuation_date", type=_date, required=True)
    value.set_defaults(handler=_handle_portfolio_value)

    history = subparsers.add_parser("history", help="show trade history")
    history.add_argument("portfolio")
    history.set_defaults(handler=_handle_portfolio_history)

    performance = subparsers.add_parser("performance", help="show performance metrics")
    performance.add_argument("portfolio")
    performance.add_argument("--from", dest="start", type=_date, required=True)
    performance.add_argument("--to", dest="end", type=_date, required=True)
    performance.set_defaults(handler=_handle_portfolio_performance)
    audit = subparsers.add_parser(
        "audit", help="reconcile cash, positions, and profit against the ledger"
    )
    audit.add_argument("portfolio")
    audit.add_argument("--date", dest="valuation_date", type=_date, required=True)
    audit.set_defaults(handler=_handle_portfolio_audit)


def _backtest_commands(subparsers: argparse._SubParsersAction) -> None:
    strategies = subparsers.add_parser("strategies", help="list available strategies")
    strategies.set_defaults(handler=_handle_backtest_strategies)

    run = subparsers.add_parser("run", help="run a strategy backtest")
    run.add_argument("strategy")
    run.add_argument("--symbols", nargs="+", required=True)
    run.add_argument("--from", dest="start", type=_date, required=True)
    run.add_argument("--to", dest="end", type=_date, required=True)
    run.add_argument("--cash", type=float, default=100000.0)
    run.add_argument("--benchmark", default="SPY")
    run.add_argument("--short-window", type=int)
    run.add_argument("--long-window", type=int)
    run.add_argument("--lookback-days", type=int)
    run.add_argument("--rebalance-days", type=int)
    run.add_argument("--top-n", type=int)
    run.add_argument("--sector-count", type=int)
    run.add_argument("--research-interval-days", type=int)
    run.add_argument("--agent-max-researches", type=int)
    run.add_argument("--agent-online-research", type=int, choices=[0, 1])
    run.add_argument(
        "--commission", type=float, help="fixed USD fee per executed order (default: 1)"
    )
    run.add_argument(
        "--slippage-bps", type=float, help="adverse fill impact in basis points (default: 5)"
    )
    run.add_argument(
        "--max-position-pct", type=float, help="maximum position weight at sizing (default: 100)"
    )
    run.add_argument(
        "--cash-reserve-pct", type=float, help="cash reserved from new buys (default: 0)"
    )
    run.add_argument(
        "--rebalance-tolerance-pct", type=float, help="skip smaller portfolio weight changes"
    )
    run.add_argument(
        "--risk-free-rate", type=float, help="annual risk-free rate for risk ratios, e.g. 0.04"
    )
    run.add_argument("--seed", type=int, help="reproducible hybrid initialization (default: 42)")
    run.add_argument(
        "--skip-days", type=int, help="recent sessions omitted from dual-momentum ranking"
    )
    run.add_argument(
        "--daily-progress", action="store_true", help="print daily values when redirecting output"
    )
    run.add_argument(
        "--check-only", action="store_true", help="check data and parameters without running"
    )
    run.set_defaults(handler=_handle_backtest_run)

    list_runs = subparsers.add_parser("list", help="list recent backtest runs")
    list_runs.add_argument("--limit", type=int, default=20)
    list_runs.set_defaults(handler=_handle_backtest_list)

    show = subparsers.add_parser("show", help="show a persisted backtest run")
    show.add_argument("run_id", type=int)
    show.set_defaults(handler=_handle_backtest_show)
    export = subparsers.add_parser("export", help="export a report to stdout or a local file")
    export.add_argument("run_id", type=int)
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--output", type=Path)
    export.set_defaults(handler=_handle_backtest_export)


def _ui_commands(subparsers: argparse._SubParsersAction) -> None:
    serve = subparsers.add_parser("serve", help="serve the local web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_handle_ui_serve)


def _handle_market_init(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    market.initialize()
    print(f"initialized {args.db}")


def _handle_market_universe(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    for symbol in market.list_symbols():
        print(symbol)


def _handle_market_sync(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    market.initialize()
    symbols = market.list_symbols() if args.all else backtests._resolve_symbols(args.symbols)
    results = market.sync(
        symbols,
        args.start,
        args.end,
        force=args.force,
        continue_on_error=not args.fail_fast,
        progress=lambda e: print(e["message"], flush=True),
    )
    for symbol, count in results.items():
        verb = "cached" if count else "already covered"
        print(f"{symbol}: {verb} {count} rows")
    if market.errors:
        raise RuntimeError(
            f"{len(market.errors)} symbols failed; other symbols were retained. See messages above."
        )


def _handle_market_add_symbol(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    symbol = market.add_symbol(
        args.symbol,
        provider_symbol=args.provider_symbol,
        asset_name=args.asset_name,
        asset_type=args.asset_type,
    )
    print(f"added symbol {symbol}")


def _handle_market_remove_symbol(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    symbol = market.remove_symbol(args.symbol)
    print(f"removed symbol {symbol}")


def _handle_market_refresh(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    results = market.refresh(args.symbols, args.days)
    for symbol, count in results.items():
        print(f"{symbol}: refreshed {count} rows")


def _handle_market_show(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    rows = market.bars(args.symbol, args.start, args.end)
    print("date open high low close volume basis")
    for bar in rows:
        print(
            f"{bar.trading_date} {bar.open:.4f} {bar.high:.4f} {bar.low:.4f} "
            f"{bar.close:.4f} {bar.volume} {bar.price_basis}"
        )


def _handle_market_price(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    bar = market.price(args.symbol, args.target_date, allow_previous=not args.exact)
    price = bar.adjusted_close if bar.adjusted_close is not None else bar.close
    print(f"{bar.symbol} {bar.trading_date} {price:.4f} basis={bar.price_basis}")


def _handle_market_replay(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    for trading_date, bars in market.replay(args.start, args.end):
        print(f"{trading_date}: {len(bars)} bars")


def _handle_portfolio_create(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    portfolio_id = portfolios.create(args.name, args.cash)
    print(f"created portfolio {args.name} id={portfolio_id} cash={args.cash:.2f}")


def _handle_portfolio_buy(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    trade_id = portfolios.buy(args.portfolio, args.symbol, args.shares, args.execution_date)
    print(f"created buy trade id={trade_id}")


def _handle_portfolio_sell(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    trade_id = portfolios.sell(args.portfolio, args.symbol, args.shares, args.execution_date)
    print(f"created sell trade id={trade_id}")


def _handle_portfolio_holdings(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    state = portfolios.state(args.portfolio, through=args.valuation_date)
    print("symbol shares average_cost")
    for symbol, position in sorted(state.holdings.items()):
        print(f"{symbol} {position.shares} {position.average_cost:.4f}")


def _handle_portfolio_cash(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    state = portfolios.state(args.portfolio, through=args.valuation_date)
    print(f"{state.cash:.2f}")


def _handle_portfolio_value(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    valuation = portfolios.value(args.portfolio, args.valuation_date)
    print(f"cash {valuation.state.cash:.2f}")
    print(f"market_value {valuation.market_value:.2f}")
    print(f"total_value {valuation.total_value:.2f}")
    print(f"realized_pnl {valuation.state.realized_pnl:.2f}")
    print(f"unrealized_pnl {valuation.unrealized_pnl:.2f}")


def _handle_portfolio_history(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    state = portfolios.state(args.portfolio)
    trades = portfolios.db.get_trades(state.portfolio_id)
    print("id date side symbol shares price cash_impact")
    for trade in trades:
        print(
            f"{trade.id} {trade.execution_date} {trade.side} {trade.symbol} "
            f"{trade.shares} {trade.execution_price:.4f} {trade.cash_impact:.2f}"
        )


def _handle_portfolio_performance(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    metrics = portfolios.performance(args.portfolio, args.start, args.end)
    for key, value in metrics.items():
        print(f"{key} {value:.6f}" if value is not None else f"{key} unavailable")


def _handle_backtest_strategies(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    for name, description in sorted(backtests.strategies().items()):
        print(f"{name}: {description}")


def _handle_backtest_run(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    params = {key: getattr(args, key) for key in PARAMETERS if getattr(args, key, None) is not None}
    if args.check_only:
        from .engine import preflight

        backtests.db.initialize()
        print(
            json.dumps(
                preflight(
                    backtests, args.strategy, args.symbols, args.start, args.end, args.cash, params
                ),
                indent=2,
            )
        )
        return

    def progress(event):
        if event["type"] == "daily_return":
            if sys.stdout.isatty():
                print("\r" + event["message"] + "\033[K", end="", flush=True)
            elif args.daily_progress:
                print(event["message"], flush=True)
        else:
            if sys.stdout.isatty():
                print("\r\033[K", end="")
            print(event["message"], flush=True)

    result = backtests.run(
        strategy_name=args.strategy,
        symbols=args.symbols,
        start=args.start,
        end=args.end,
        starting_cash=args.cash,
        parameters=params,
        benchmark_symbol=args.benchmark,
        progress=progress,
    )
    print(f"run_id {result.run_id}")
    print(f"portfolio {result.portfolio_name}")
    print(f"strategy {result.strategy_name}")
    print(f"trades_executed {result.trades_executed}")
    for key, value in result.metrics.items():
        print(f"{key} {value:.6f}" if value is not None else f"{key} unavailable")
    benchmark_return = result.benchmark.get("total_return")
    if benchmark_return is None:
        print(f"benchmark {result.benchmark.get('symbol')} unavailable")
    else:
        print(f"benchmark {result.benchmark.get('symbol')} {benchmark_return:.6f}")


def _handle_backtest_list(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    backtests.db.initialize()
    print("id strategy start end created_at")
    for row in backtests.db.list_backtest_runs(args.limit):
        print(
            f"{row['id']} {row['strategy_name']} {row['start_date']} {row['end_date']} {row['created_at']}"
        )


def _handle_backtest_show(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    backtests.db.initialize()
    row = backtests.db.get_backtest_run(args.run_id)
    if row is None:
        raise LookupError(f"backtest run not found: {args.run_id}")
    print(f"id {row['id']}")
    print(f"strategy {row['strategy_name']}")
    print(f"start {row['start_date']}")
    print(f"end {row['end_date']}")
    print(f"created_at {row['created_at']}")
    print("parameters")
    print(json.dumps(json.loads(row["parameters_json"]), indent=2, sort_keys=True))
    print("result_summary")
    print(json.dumps(json.loads(row["result_summary_json"]), indent=2, sort_keys=True))


def _handle_ui_serve(
    args, market: MarketService, portfolios: PortfolioService, backtests: BacktestService
) -> None:
    run_server(db_path=args.db, host=args.host, port=args.port)


def _handle_market_coverage(args, market, portfolios, backtests):
    market.initialize()
    print("symbol sessions first last")
    for row in market.db.coverage_report():
        print(row["symbol"], row["bar_count"], row["first_date"] or "-", row["last_date"] or "-")


def _handle_model_audit(args, market, portfolios, backtests):
    from .model_validation import audit_models
    from .walk_forward import audit_registry

    print(json.dumps({"active": audit_registry(), "legacy_unused": audit_models()}, indent=2))


def _handle_cap_refresh(args, market, portfolios, backtests):
    from .market_caps import MarketCapService

    market.initialize()
    print(json.dumps(MarketCapService(market.db).refresh(), indent=2))


def _handle_market_expand(args, market, portfolios, backtests):
    from .market_caps import MarketCapService

    market.initialize()
    service = MarketCapService(market.db)
    print(json.dumps(service.expand(args.count), indent=2))
    if args.seed_output:
        rows = service.ranked(args.count, active_only=False)
        args.seed_output.write_text(
            json.dumps(
                {"source": service.status(), "symbols": sorted(r["symbol"] for r in rows)}, indent=2
            )
            + "\n"
        )


def _handle_model_train(args, market, portfolios, backtests):
    from .training import train_walk_forward

    market.initialize()
    train_walk_forward(
        market.db,
        args.start,
        args.end,
        args.first_test_year,
        args.last_test_year,
        args.epochs,
        args.output,
        progress=lambda message: print(message, flush=True),
    )


def _handle_backtest_export(args, market, portfolios, backtests):
    backtests.db.initialize()
    report = backtest_report(backtests.db, args.run_id)
    data = export_csv(report) if args.format == "csv" else json.dumps(report, default=str, indent=2)
    if args.output:
        args.output.write_text(data, encoding="utf-8")
        print(f"exported {args.output}")
    else:
        print(data)


def _handle_portfolio_audit(args, market, portfolios, backtests):
    report = portfolios.audit(args.portfolio, args.valuation_date)
    print(json.dumps(report, indent=2))
    if not report["reconciled"]:
        raise ArithmeticError("portfolio did not reconcile")


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc
