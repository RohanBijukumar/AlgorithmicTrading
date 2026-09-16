# AlgorithmicTrading Help

README.md is the step-by-step run guide. This file describes commands, service functions, and their behavior. All dates are ISO YYYY-MM-DD; money is USD.

## Command Format

```text
python3 -m algotrading [--db PATH] RESOURCE COMMAND [OPTIONS]
python3 -m algotrading RESOURCE COMMAND --help
```

The database option precedes the resource. Defaults point to data/trading_simulator.sqlite3. Dates and numeric settings are validated; errors exit with status 1. Shell users should quote selectors if their shell treats @ specially.

## Market Commands

| Command | Purpose and example |
| --- | --- |
| market init | Create/migrate local tables and seed missing default symbols |
| market universe | Print all active canonical tickers |
| market coverage | Print each ticker's row count and first/last stored dates |
| market cap-refresh | Download a bulk Nasdaq USD market-cap snapshot; retain the old snapshot if the refresh fails |
| market expand --count 1500 | Add up to N currently ranked US-listed companies; retain existing symbols and deactivations |
| market add-symbol VOO | Add/reactivate VOO. Optional --provider-symbol, --name, --asset-type |
| market remove-symbol VOO | Deactivate VOO; retain historical data and trades |
| market sync --symbols SPY QQQ --from 2023-01-01 --to 2025-12-31 | Download/cache daily bars for specified symbols |
| market sync --all --from 2023-01-01 --to 2025-12-31 | Download the active universe sequentially |
| market refresh --symbols SPY --days 10 | Force-refresh a trailing calendar-day window |
| market show SPY --from 2024-01-01 --to 2024-01-31 | Print stored daily OHLCV |
| market price SPY --date 2024-03-02 | Return the latest stored price on or before a date |
| market price SPY --date 2024-03-01 --exact | Require an exact stored session |
| market replay --from 2024-01-01 --to 2024-01-31 | Group stored bars chronologically by session |

Sync --force refreshes the requested data range; it is not a database reset. Normal sync skips covered historical requests, including completed requests bounded by holidays. One provider failure does not discard successful symbols. --fail-fast stops at the first failure; the normal CLI still returns an error at the end if any symbols failed.

Adding a ticker never implicitly downloads its history. The provider is Yahoo Chart. Provider failures and ranges are recorded locally. There is no paid API or language-model cost.

## Portfolio Commands

| Command | Purpose |
| --- | --- |
| portfolio create --name demo --cash 100000 | Create a cash-funded manual portfolio |
| portfolio buy demo SPY --shares 10 --date 2024-03-01 | Buy whole normalized units at the exact adjusted close |
| portfolio sell demo SPY --shares 5 --date 2024-06-03 | Sell held units; record average-cost realized P&L |
| portfolio holdings demo --date 2024-12-31 | Reconstruct holdings through a date |
| portfolio cash demo --date 2024-12-31 | Reconstruct cash through a date |
| portfolio value demo --date 2024-12-31 | Show cash, market value, equity, and P&L |
| portfolio history demo | Print the chronological fill ledger |
| portfolio performance demo --from 2024-01-01 --to 2024-12-31 | Compute session-based risk/return statistics |
| portfolio audit demo --date 2024-12-31 | Reconcile cash, holdings, and P&L identities |

Manual trades require exact stored trading dates, positive integer quantities, sufficient cash/shares, and chronological insertion. They do not silently move to a previous date. Manual commissions are zero. Backtest portfolios are read-only after completion; create another portfolio for manual orders.

Adjusted-price accounting uses normalized share units. Dividends/splits are embedded in the price series, not cash/share ledger events. Realized P&L uses weighted average cost, not FIFO tax lots.

## Backtest Commands

### Discover and Check

```bash
python3 -m algotrading backtest strategies
python3 -m algotrading backtest run dual-momentum --symbols SPY QQQ TLT GLD --from 2024-01-01 --to 2025-12-31 --check-only
```

--check-only validates the strategy and reports actual in-window observations, exclusions, warm-up gaps, effective parameters, and research limitations. It creates no run or portfolio.

### Run

```bash
python3 -m algotrading backtest run momentum --symbols @universe --from 2024-01-01 --to 2025-12-31 --cash 100000 --lookback-days 126 --rebalance-days 21 --top-n 10 --commission 1 --slippage-bps 5
```

Symbols may be space-separated tickers or a single selector. @universe uses all active symbols. @topN accepts any positive integer and ranks active companies by numeric Nasdaq USD market caps. It works for CLI/web backtests and market sync. A 24-hour cache avoids per-stock requests; expired or missing caps trigger one bulk refresh. If that fails, selection fails with a refresh instruction. Missing caps, ETFs, and known alternate classes are excluded; requesting more ranked companies than available fails explicitly. These are current US-listed rankings, not global exchange coverage or historical index membership. Nasdaq does not always supply a quote timestamp, so fetched_at is the retrieval time, not a guaranteed market-data timestamp.

Signals use the current close and fill at the next observed session's adjusted open. The final close cannot create a same-day fill. Missing execution bars expire an order. Buys are resized when costs, a price gap, or risk limits reduce affordability. Sell orders precede buys.

| Argument | Meaning |
| --- | --- |
| --cash | Starting capital, default 100000 |
| --benchmark | Reference ticker, default SPY |
| --short-window / --long-window | Fast/slow averages for moving-average |
| --lookback-days | Sessions used by the selected indicator |
| --rebalance-days | Sessions between allocation decisions |
| --top-n | Maximum selected positions |
| --skip-days | Recent sessions omitted from dual-momentum ranking |
| --sector-count | Number of selected neural sectors |
| --commission | USD per executed order, default 1 |
| --slippage-bps | Adverse fill adjustment, default 5; 1 bp = 0.01% |
| --max-position-pct | Position allocation limit when sizing buys, default 100 |
| --cash-reserve-pct | Cash excluded from new buys, default 0 |
| --rebalance-tolerance-pct | Skip small allocation changes, default 0 CLI / 0.5 UI |
| --risk-free-rate | Annual risk-free rate for Sharpe/Sortino, e.g. 0.04; no cash interest credited |
| --seed | Hybrid random seed, default 42 |
| --research-interval-days | Minimum discovery cadence in observed sessions |
| --agent-max-researches | Maximum discovery decisions, default 4, maximum 20 |
| --agent-online-research 0 or 1 | Opt into bounded Yahoo fetches during discovery; default off |
| --daily-progress | Include daily values when output is redirected |
| --check-only | Validate without running |

In interactive terminals, daily equity updates on one line. Trade and strategy-switch messages remain separate. Ctrl+C interrupts the CLI simulation and removes its incomplete portfolio.

### Strategy Behavior and Defaults

| ID | Rule | Typical defaults |
| --- | --- | --- |
| buy-and-hold | Initial equal allocation, no rebalances | SPY baseline |
| equal-weight | Equal-value periodic allocation | 21-session rebalance |
| moving-average | Hold one symbol when fast average exceeds slow | 50 / 200 |
| momentum | Highest trailing total return | 126 lookback, 21 rebalance, top 10 |
| mean-reversion | Lowest trailing return | 21 lookback, 5 rebalance, top 10 |
| low-volatility | Lowest trailing return standard deviation | 63 / 21 / 10 |
| breakout | Current close above previous window high | 55 / 5 / 10 |
| trend-following | Strongest returns above trailing average | 126 / 21 / 10 |
| rsi-strength | Highest rolling-window RSI | 14 / 21 / 10 |
| dual-momentum | Positive skipped-month momentum and long-term trend | 252 / 21 / top 3, skip 21 |
| time-series-momentum | Positive trailing returns, inverse-volatility weighting | 252 / 21 |
| inverse-volatility | Unlevered inverse-volatility allocation | 63 / 21 |
| nn-pattern | Frozen neural stock scores | 63 / 21 / 10 |
| nn-sector-rotation | Frozen neural scores aggregated by sector | 63 / 21 / 10, 3 sectors |
| nn-risk-adjusted | Neural score less volatility penalty | 63 / 21 / 10 |
| hybrid | Choose among shadow strategies using net window gains | 63 / 21 / 10, seed 42 |
| agentic-research | Curated OHLCV discovery screen | 63 lookback, 63 interval, 4 decisions, top 5 |

RSI uses rolling average gains/losses over the selected window, not Wilder recursive smoothing. Flat prices produce 50. Warm-up uses earlier cached data but does not enter positions before the backtest starts; gaps reset usable indicator history.

Time-series momentum is a long-only adaptation and holds cash when no asset passes. Inverse volatility is not full risk parity because it does not solve for correlated risk contributions.

Hybrid runs separate shadow portfolios, including the same costs and execution delay. Historical winning frequency contributes a bounded score bonus. Seeds reproduce the starting choice when data, artifacts, and settings are unchanged.

The neural models require 64 bars and use ticker-anonymous price/volume features. Three pretrained 7-input/12-hidden/2-output networks serve each annual fold. The stock head predicts forward market-relative returns; the sector head predicts the sector's average relative return. The 2024 cutoff is removed. The simulated year selects its pre-year trained/validated ensemble, and a model-fold event appears in CLI/UI activity. A missing fold yields no neural selections; existing positions rebalance toward cash on the next scheduled decision. No future-trained fallback or runtime fitting occurs. Sector mapping remains static, including Nasdaq sector hints for added companies. See nn_viability.md for the full protocol and limits.

Discovery is not an LLM analyst. It screens a curated queue, records TAKE only for a positive composite, and otherwise records PASS. Online mode may fetch one candidate's historical Yahoo series in a research window. The model can read only data at or before its simulated clock.

### Inspect and Export

```bash
python3 -m algotrading backtest list --limit 20
python3 -m algotrading backtest show 1
python3 -m algotrading backtest export 1 --format json --output data/report.json
python3 -m algotrading backtest export 1 --format csv --output data/equity.csv
```

Without --output, export writes to stdout. JSON includes summary, daily equity, fills, and discovery decisions. CSV includes date, cash, market value, total value, return, drawdown, and benchmark value.

Performance includes first-day costs, marks drawdowns from initial capital, and uses observed sessions for annualization. Undefined Sharpe/Sortino values show unavailable; CAGR requires at least 252 sessions. Sell-fill win rate measures profitable sales, not complete round trips.

New reports retain their own daily valuations after later data refreshes. Legacy summaries are explicitly labeled and should be rerun before comparing with current execution.

## Neural Model Audit

```bash
python3 -m algotrading model audit
python3 -m algotrading model train --from 1990-01-01 --to 2026-09-06 --first-test-year 1996 --last-test-year 2026 --epochs 40
python3 tools/evaluate_neural.py --db data/trading_simulator.sqlite3 --output data/neural_evaluation_v2.json
```

`model audit` prints the active registry's SHA256, fold years, training code/data hashes, regularization, and limitations. It also reports the old v1 artifacts under legacy_unused; these are no longer used by strategies. New neural/hybrid reports embed the active registry audit, pinned for the duration of the run.

`model train` requires the optional PyTorch training dependency (`python3 -m pip install -e '.[training]'`). It copies the local database to a consistent temporary snapshot, builds realized 21-session targets, and trains annual folds offline. --from/--to bound data used; --first-test-year/--last-test-year bound deployment folds; --epochs sets the maximum epochs per member (1-500). --output defaults to the bundled models/walk_forward_v2.json; an alternative output is an experiment and is not automatically activated. The default inference registry is replaced atomically only after all requested eligible folds validate. Insufficient-history years are reported and skipped. No network calls or portfolio mutations occur during training.

Each fold uses earlier training data and the preceding year for validation, purging labels across both boundaries. Three fixed seeds (42/43/44) use expanding, 10-year, and 5-year training histories. Train-only scaling, AdamW decay 0.03, dropout 0.1, gradient clipping, early stopping (patience 8), and year-balanced loss constrain fitting. Held-out test loss is computed only after fitting and cannot select checkpoints. Rerunning or selecting experiments after inspecting their test results still creates research overfitting.

`evaluate_neural.py` compares fixed neural variants, simple momentum, and SPY across yearly windows from 2024 through available 2026 SPY data. It also stresses neural transaction costs. It uses a temporary database copy, makes no downloads, and writes only the requested JSON output. The output includes exploratory block-bootstrap intervals, not a multiple-testing-adjusted deployment decision. See [nn_viability.md](nn_viability.md) for measured results and the proposed promotion protocol.

## Web UI

```bash
python3 -m algotrading ui serve --host 127.0.0.1 --port 8000
```

Open the printed URL. Ctrl+C stops the server. It is a local single-user server without authentication; leave the default loopback host for normal use.

| Page | Main actions |
| --- | --- |
| Overview | Inspect readiness, download starter data, open recent runs, choose a preset |
| Market | Plot up to 12 symbols, select price/return/OHLC/RSI/volume, search coverage, sync/cancel |
| Strategy Lab | Select strategy, edit relevant settings, check coverage, prepare a sync, run/cancel, inspect live equity |
| Portfolios | Create accounts, submit manual trades, inspect historical holdings, sort/page tables, delete |
| Run Library | Filter saved runs, compare 2-4 matching windows, open reports, rerun, export, delete |

Chart hover boxes overlay the page. Market hover selects the nearest line on the hovered date. Portfolio hover shows value, cash, market value, and the five largest fills for that date.

Table defaults show 10 rows; 11-14 total rows fit on one default page. Row sizes can be increased through 100. Holdings and trade columns sort ascending/descending.

Jobs continue after browser refresh; session storage reconnects to the job. Server restarts lose in-memory job state. Completed reports remain in SQLite. Only one sync and one backtest run concurrently. Activity buffers are bounded, daily metrics are coalesced, and the visible log retains the latest 250 events; full fills remain in the ledger.

Theme and last submitted backtest settings persist in the browser. Controls have brief hover descriptions. Reference explains key terms and simulation assumptions.

## Python Service Reference

Instantiate Database(path), call initialize(), then use the same services as the CLI. Internal functions prefixed with an underscore are implementation details; prefer the APIs below.

### Database

| Function | Purpose |
| --- | --- |
| initialize() | Apply additive schema changes and seed symbols |
| connect() | Context-managed SQLite connection, with commit/rollback |
| transaction() | Serialize validation and multiple writes; reuse the connection inside the block |
| add_symbol(symbol, provider_symbol=None, asset_name=None, asset_type="equity", active=True) | Add/update symbol metadata |
| list_symbols(active_only=True), get_symbol(symbol), require_symbol(symbol) | Read/validate active membership |
| deactivate_symbol(symbol) | Disable future selection without removing history |
| insert_market_bars(bars, fetched_at=None) | Validate and upsert daily bars |
| record_fetch(result) | Persist provider success/failure metadata |
| get_coverage(symbol), coverage_report() | Single-symbol range or universe coverage summary |
| get_bars(symbol, start, end), get_bar_on_or_before(symbol, date) | Historical tape access |
| trading_dates(start, end, symbols=None), latest_trading_date() | Observed calendar queries |
| create_portfolio(name, starting_cash), get_portfolio(name), list_portfolios() | Account persistence |
| insert_trade(...), get_trades(portfolio_id, through=None) | Low-level ledger persistence; use PortfolioService for validated orders |
| save_snapshots(portfolio_id, points), get_snapshots(portfolio_id) | Preserve completed backtest equity and holding marks |
| insert_backtest_run(...), get_backtest_run(id), list_backtest_runs(limit=20) | Run persistence |
| delete_portfolio(name), delete_backtest_run(id) | Deletion with the ownership behavior described above |
| insert_research_decision(...), list_research_decisions(portfolio_id) | Discovery audit trail |

### MarketService(db, provider)

- initialize(), list_symbols(): initialize storage or list active tickers.
- add_symbol(...), remove_symbol(symbol): maintain the universe.
- sync(symbols, start, end, force=False, progress=None, continue_on_error=False, cancelled=None): fetch sequentially; errors are available in service.errors. API/CLI wrappers enable continued sync by default.
- refresh(symbols, days): force-refresh a trailing window.
- bars(symbol, start, end): return stored bars from the configured provider.
- price(symbol, date, allow_previous=True): exact or prior-session quote lookup.
- replay(start, end): group bars into chronological session batches.
- YahooChartMarketDataProvider.fetch_daily(symbol, start, end, provider_symbol=None): return a structured FetchResult without changing portfolio state.

### PortfolioService(db)

- create(name, starting_cash): create a portfolio.
- buy(name, symbol, shares, date), sell(...): validate and atomically append a manual fill.
- state(name, through=None): reconstruct cash, holdings, and realized P&L.
- value(name, valuation_date): value holdings; completed backtests use frozen snapshots.
- value_history(name, end, start=None, max_points=260): retrieve/sweep chart values; respect the requested maximum, including endpoints.
- performance(name, start, end): calculate trading-session risk/return metrics.
- audit(name, valuation_date): verify ledger identities and aggregate holdings.

### BacktestService(db)

- strategies(): map strategy IDs to descriptions.
- catalog(): return UI-friendly names, categories, defaults, and rules.
- run(strategy_name, symbols, start, end, starting_cash, parameters=None, benchmark_symbol="SPY", progress=None, cancelled=None): validate and execute the simulation, returning BacktestResult.
- engine.preflight(service, strategy_name, symbols, start, end, starting_cash=100000, parameters=None): coverage and parameter check without trades.
- strategy_config.validate_parameters(strategy, parameters=None): merge defaults and validate every supplied setting.

### Accounting and Simulation

- Ledger(starting_cash).apply(trade): update average-cost positions, cash, realized P&L, fees, and per-symbol attribution.
- Ledger.mark(prices): calculate equity/unrealized P&L and enforce the accounting identity.
- MarketWindow(db, symbols, end): cache series; frontier limits visible history.
- MarketWindow.get_bars(...), get_bar_on_or_before(...), trading_dates(...): cached date queries.
- MarketWindow.contiguous_bars(symbol, current): history after the last missing observed session.
- MarketWindow.reload(symbol): invalidate relevant caches after an explicit provider refresh.
- MarketCapService.refresh(payload=None): validate and atomically replace the bulk cap cache; supplied JSON supports offline fixture/import workflows.
- MarketCapService.status(): report source, retrieval timestamp, currency, and coverage scope without fetching.
- MarketCapService.ranked(count, active_only=True): ensure a current cache, exclude ineligible listings, return descending numeric caps; reject oversized active-universe requests.
- MarketCapService.expand(count=1500): add ranked company membership and sector hints without reactivating removals or downloading bars. CLI --seed-output writes reusable membership only, never a hardcoded ranking.
- SimulationBroker.execute(orders, date, progress=None): expire missing quotes, resize orders, apply costs, and produce fills.
- SimulationBroker.state(...), value(...): read simulated balances and close marks.
- SimulationBroker.persist(db, points): write a successful ledger and snapshots inside the caller's transaction.
- BacktestCancelled: cooperative cancellation signal handled by CLI/web wrappers.

### Analytics, Reports, and Models

- performance_metrics(points, starting_value, risk_free_rate=0): session-based return, drawdown, risk ratios, and CAGR.
- enrich_curve(points, starting_cash): add cumulative return and drawdown to each point.
- monthly_returns(points, starting_cash): derive compounded monthly returns from month-end equity.
- reporting.backtest_report(db, run_id): assemble a portable run, equity, fill, and research payload.
- reporting.export_csv(report): serialize daily equity to CSV.
- reporting.run_record(row, compact=False): parse database JSON fields; optionally omit large report arrays.
- nn_models.load_model(name), validate_model(model), model_metadata(name): load, validate, and inspect frozen artifacts.
- nn_models.neural_features(bars): calculate seven normalized features from the latest 64 bars.
- nn_models.neural_score(name, bars, as_of=None, registry=None): pure-Python walk-forward ensemble inference; None means insufficient history or no eligible year. Optional registry pins the run's model version.
- nn_models.sector_for_symbol(symbol, provider_sector=None): static sector bucket with a normalized provider hint fallback.
- walk_forward.load_registry(), validate_registry(registry), fold_for_date(as_of, registry=None): load/validate annual models and select only the matching year.
- walk_forward.predict_member(member, features), score(name, features, as_of, registry=None): apply train-only normalization and frozen ensemble inference.
- walk_forward.audit_registry(registry=None): active model checksum, provenance, fold coverage, and limitations.
- training.build_samples(db, start, end, stride=10): ticker-blinded features and realized next-open-to-21st-close market-relative/sector targets; require contiguous bars.
- training.split_samples(samples, year, history_years=None): earlier training, prior-year validation, and held-out test rows, grouped by date and purged by actual label endpoints.
- training.train_walk_forward(db, start, end, first_year, last_year, epochs, output, progress=print): offline snapshot, regularized fitting, blind fold evaluation, and atomic registry export.
- model_validation.audit_models(): report artifact fingerprints, declared dates, and missing reproducibility metadata; no training.
- model_validation.purged_walk_forward(dates, label_horizon=21, minimum_training=504, validation_size=126, test_size=126): return chronological train/validation/test index lists over unique observed sessions. Leave label-horizon gaps before validation/test and enough trailing history to realize test labels. Returns an empty list if history is insufficient; map each session to all corresponding stock rows.
- model_validation.purge_label_overlap(samples, boundary): retain sample dictionaries with feature_date <= label_end < boundary. Use actual date values and combine this with the intended fold's session membership; it does not fit a model.

### Web and Jobs

- run_server(db_path, host="127.0.0.1", port=8000): serve the workspace.
- JobStore.create(total=0): allocate a bounded-concurrency background job.
- JobStore.emit(id, event): append activity or replace latest daily metrics.
- JobStore.snapshot(id, after=0): return incremental events, latest metrics, and a sampled curve.
- JobStore.cancel(id), cancelled(id): signal/read cooperative cancellation.
- JobStore.update(id, **fields): store completion, failure, or result.

HTTP GET endpoints include /api/overview, /api/market/coverage, /api/market/bars, /api/portfolios, /api/backtests/strategies, /api/backtests, /api/backtests/ID/report, and /api/backtests/ID/export?format=csv. POST /api/backtests/preflight validates a backtest; POST /api/backtests and /api/market/sync start jobs. Poll /api/backtest-jobs/ID?after=CURSOR or /api/market-sync-jobs/ID?after=CURSOR; POST the job's /cancel endpoint to request cancellation.

### Data Objects

MarketBar contains symbol, trading_date, OHLC, adjusted_close, volume, provider, interval, and basis. FetchResult contains request ranges, bars, fetch timestamp, and success/error. Trade contains portfolio, side, units, execution date/price, cash impact, fee, slippage, and timestamp. PortfolioState/Valuation/ValuePoint describe reconstructed or frozen balances. BacktestResult contains the saved run ID, portfolio, dates, fill count, metrics, and benchmark.

## Verification

```bash
python3 -m unittest discover -s tests
python3 tools/benchmark.py --db data/trading_simulator.sqlite3 --symbols SPY QQQ IWM
```

The benchmark copies the source SQLite database into a temporary directory, evaluates fixed defaults, prints timing/performance/accounting checks, then removes only that temporary copy. It performs no downloads and changes no existing research results.

See [verification notes](docs/validation.md) for frontend checks, measured runtimes, and known verification limits.
