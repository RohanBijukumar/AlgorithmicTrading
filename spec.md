# AlgorithmicTrading Specification

## Product and Scope

AlgorithmicTrading is an offline-first, local equity/ETF research simulator with interchangeable web and command-line interfaces. Its core workflow is: inspect data coverage, download missing history, establish a baseline, run a strategy with explicit costs, inspect risk and attribution, and compare reproducible results.

Version 2 improves research usability and execution correctness. It does not claim profitability or live-trading readiness. Older stage specifications are superseded by the contracts below.

## Architecture and Storage

- Python 3.11+ with a standard-library HTTP server and CLI.
- HTML, CSS, and JavaScript frontend; locally vendored Lucide icons. No CDN is required while using the application.
- SQLite at data/trading_simulator.sqlite3 by default, configurable with --db before the resource command.
- WAL mode and a 30-second connection timeout. Manual validation and insertion run under BEGIN IMMEDIATE to serialize competing writers.
- Existing schemas migrate additively. Existing data, ledgers, and run summaries are retained.
- Database connections are scoped to operations; no connections or mutable strategy instances are shared between simulation threads.
- The market window loads each selected series once and answers date lookups with binary search. Its frontier hides future bars.
- Simulation accounting runs in memory. Trades, daily snapshots, and the run summary persist together in a transaction on success.
- Manual chart histories use one chronological ledger sweep. Backtests use their saved snapshots.
- Large completed jobs are evicted; activity retention is bounded and polled incrementally.

### Tables

| Table | Responsibility |
| --- | --- |
| symbols | Canonical/provider ticker, optional name, asset type, active status |
| market_bars | Daily raw OHLCV, optional adjusted close, provider, basis, fetch time |
| provider_fetches | Requested ranges, returned row count, success/failure |
| portfolios | Name, starting capital, creation time |
| trades | Immutable fills, execution price, cash impact, fee, slippage |
| portfolio_snapshots | Daily cash, market/total value, realized/unrealized P&L, individual holding values |
| backtest_runs | Strategy, requested window, effective settings, result summary |
| research_decisions | Discovery verdicts, evidence date, score, rationale, data source |

Backtest snapshots are fixed after completion. Refreshing market data does not rewrite recorded outcomes. Legacy runs without snapshots use reconstructed chart history and retain an explicit legacy label.

## Market Data

Yahoo Chart is the specific daily OHLCV provider. Its public endpoints are not an exchange-grade data contract. Provider code remains isolated; no paid subscriptions or LLM calls are made.

- Universe membership is database-backed. A diversified seed includes equities and equity, bond, gold, and international ETFs.
- Adding a symbol does not download data. Deactivation retains its history.
- @universe selects active symbols. @topN accepts any positive integer and ranks active US-listed companies using the latest Nasdaq screener USD caps, cached for 24 hours. Missing/expired snapshots require a successful bulk refresh; never fall back to a static list or silently stale caps. Exclude unavailable caps, ETFs, and known duplicate share classes; reject requests exceeding eligible membership. Current caps are not historical rankings.
- Bundle a broad membership seed of roughly 1,500 companies. Market expansion adds ranked members without deleting existing symbols or reactivating removals; membership and cap snapshots are separate from price downloads. Preserve the user's BK/PARA exclusions. Display cap values and retrieval dates; do not invent a provider as-of date when unavailable.
- Date range and finite positive OHLC, consistent high/low, and nonnegative volume are validated.
- Forced sync refreshes only the requested symbol ranges; it does not reset storage.
- Sequential requests avoid unbounded concurrency. Provider failures are retained individually and the web/CLI sync proceeds to other symbols. CLI --fail-fast stops immediately.
- Successful historical request ranges prevent repeated fetches solely because the range begins or ends on a holiday/weekend.
- UI coverage exposes first/last stored session and row count, with pre-start warm-up counts in preflight.
- No automatic network request occurs during ordinary backtesting. Discovery mode is the explicit, bounded exception.

## Accounting Contract

Long-only, cash-funded positions with whole normalized units. No leverage, shorts, deposits, withdrawals, taxes, interest, or brokerage execution.

- Shared Ledger applies all manual and simulated fills.
- Buy cash impact = -(shares * fill price) - commission.
- Buy average cost includes commission.
- Sell cash impact = shares * fill price - commission.
- Realized P&L = net sell proceeds - sold units * average cost.
- Unrealized P&L = marked market value - remaining average cost.
- Equity = cash + marked market value.
- Equity - starting cash = realized P&L + unrealized P&L, within floating-point tolerance.
- Every execution verifies cash and position sufficiency and reconciles recorded fees.
- Portfolio audit also checks that individual holding values sum to aggregate market value.
- Names must be nonempty, capital finite/nonnegative, and share counts positive integers.
- Backdated manual inserts and non-session manual executions are rejected. Simultaneous orders cannot spend the same funds twice.
- Manual fills use the exact session's adjusted close and zero explicit cost; backtests have a distinct next-open execution model.
- Deleting a portfolio removes its ledger, snapshots, research, and linked reports. Deleting a run summary alone retains its portfolio.

### Price Basis

Fill open = raw open * (adjusted close / raw close), then adverse slippage. Close marks use adjusted close where available.

This is a consistently normalized price simulation. It does not simulate actual split share changes, dividend payment dates, or tax lots. An adjusted dollar budget may imply a different count than historical broker shares. The limitation is visible in reports and reference documentation.

## Backtest Timeline

1. Validate dates, strategy, capital, parameters, model cutoff, and universe.
2. Identify symbols with actual observations inside the requested window and report excluded symbols.
3. Load cached history, including pre-start warm-up. Build the selected universe's observed session calendar.
4. On each session, execute orders generated at the prior observed close at this session's adjusted open.
5. Missing exact execution-session bars expire orders; stale quotes never produce fills.
6. Apply commission/slippage, sell before buying, and resize buys for available funds, reserve cash, and position limits.
7. Mark positions at the close, reconcile accounting, and emit the day's equity and performance snapshot.
8. Generate orders from information available at this close. Do not generate unfillable final-session orders.
9. Persist successful results atomically. Failure/cancellation removes the newly created simulation portfolio.

A symbol cannot enter before its first observed close. Indicators require enough contiguous observed sessions; missing sessions reset the usable history. Missing marks on existing holdings carry the previous available price and generate a report warning, not a fabricated liquidation.

Buy-and-hold trades once after the first observed close. It does not automatically retry a missing second-session fill or retroactively buy newly listed symbols.

### Risk and Cost Controls

| Parameter | Default | Meaning |
| --- | --- | --- |
| commission | $1 | Fixed fee per executed order |
| slippage_bps | 5 | Adverse open-price adjustment |
| max_position_pct | 100 | Maximum weight at buy sizing; prices may drift later |
| cash_reserve_pct | 0 | Cash excluded from new buys |
| rebalance_tolerance_pct | 0 CLI / 0.5 UI | Portfolio-weight band to suppress small rebalance orders |
| risk_free_rate | 0 | Annual rate for Sharpe/Sortino, not interest credited |
| seed | 42 | Reproducible hybrid initialization |

Caps may intentionally leave substantial cash when the strategy chooses few symbols. Fees, integer units, missing fills, warm-up, and defensive filters can also create cash drag.

## Strategies

The catalog and validation schema are shared by CLI and web. Presets are starting points, not optimized recommendations.

| Strategy | Behavior |
| --- | --- |
| buy-and-hold | Equal starting allocation, no rebalances |
| equal-weight | Periodic equal allocation |
| moving-average | One-symbol fast/slow moving-average crossover |
| momentum | Rank trailing total returns |
| mean-reversion | Rank trailing losers; experimental reversal |
| low-volatility | Select lowest trailing realized volatility |
| breakout | Select new trailing price highs |
| trend-following | Positive price-vs-moving-average trends, ranked by return |
| rsi-strength | Rank highest rolling-window RSI |
| dual-momentum | Rank 252-session momentum, skip recent 21 sessions, require positive score and price above trailing average |
| time-series-momentum | Long positive trailing-return assets, inverse-volatility weights |
| inverse-volatility | Unlevered inverse-volatility allocation, without a correlation model |
| nn-pattern | Frozen neural stock scores |
| nn-sector-rotation | Neural stock scores grouped by static sector taxonomy |
| nn-risk-adjusted | Neural score minus a trailing-volatility penalty |
| hybrid | Seeded adaptive selection among shadow portfolios |
| agentic-research | Curated candidate OHLCV discovery screen, optionally with bounded Yahoo fetches |

The new strategies are simplified adaptations, not exact replications or guarantees. Trend methodology is informed by [Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum). Inverse-volatility allocation is not full equal-risk-contribution optimization: [Understanding Risk Parity](https://www.aqr.com/-/media/AQR/Documents/Insights/White-Papers/Understanding-Risk-Parity.pdf).

### Hybrid and Neural Behavior

Hybrid maintains one shadow broker per eligible candidate, using the same fill/cost rules. Window gains are marked net portfolio returns. Historical winning frequency adds a bounded bonus of at most 0.5 percentage points to the selection score. Switch messages name the strategies and the net window result. Cash/no-signal candidates remain valid comparisons.

Neural inference uses a frozen registry with 31 annual folds (1996-2026 initially), each containing three PyTorch-trained 7/12/2 networks. Inputs use only the latest 64 observations and omit symbol IDs. Two outputs predict 21-session market-relative and sector-average returns. Sector mapping is static. A simulated year uses only its fold; training and validation labels must end before that year. Remove the old 2024 start restriction. Missing folds give explicit warnings and no neural selections, never a future-trained fallback. Backtests pin the registry and emit annual model-fold activity; they never train.

Offline training takes a consistent SQLite snapshot and applies date-grouped, purged walk-forward cross-validation. For test year Y, validation features come from Y-1 and outcomes must end before Y; training outcomes must end before Y-1. Models use expanding, 10-year, and 5-year training histories with fixed seeds 42/43/44. Train-only normalization, AdamW decay 0.03, dropout 0.1, gradient clipping, early stopping, and equal-year weighting regularize fitting. Test outcomes are scored only after training and never choose weights or epochs. The registry records sample/code hashes, split boundaries, realized label endpoints, selected epochs, and test losses. Export atomically after validation. Earlier test years can become later training/validation history, as in real chronological retraining; folds are not independent experiments.

The old v1 files remain inspectable for historical provenance but are unused by active strategies. Fold count and regularization cannot guarantee freedom from overfitting. Current constituents/caps, static sectors, vendor history revisions, and repeated experimentation still bias research. Untouched forward/paper evaluation remains required. See nn_viability.md.

Discovery reviews a limited number of candidates, accepts a positive composite score, and records TAKE/PASS decisions. It is not a web-news analyst, sentiment model, or LLM agent. Online data is opt-in, with at most one fetch in a research window and a maximum 20-decision budget.

## Performance and Reports

- Daily metrics use observed trading sessions, excluding fabricated weekends.
- Initial capital is included in the first daily return and drawdown peak.
- Return and drawdown are net of fees and slippage.
- Volatility/Sharpe use 252-session annualization; Sortino uses downside deviation.
- Undefined ratios are null, displayed as a dash. CAGR is omitted until at least 252 sessions.
- Benchmark is a fractional, frictionless buy-and-hold entered at the same first eligible next-open session. Missing benchmark sessions make the aggregate comparison unavailable.
- Reports include monthly returns, realized-plus-unrealized attribution, cash/exposure, fill costs, skipped orders, parameter values, runtime, actual dates, and data warnings.
- CSV exports daily equity; JSON exports summary, equity, trades, and research.
- Comparisons require matching requested dates and execution-engine versions.
- Legacy summaries are never silently rewritten into new-engine results.

## Web Experience

Overview, Market, Strategy Lab, Portfolios, and Run Library provide the primary navigation. The interface uses restrained dark/light themes, translucent navigation/tooltips, and green/red signs for gains/losses; numerical signs remain visible for accessibility.

- Overview: data readiness, starter download, recent results, research presets.
- Market: multi-symbol chart, price/return/OHLC/volume/RSI metrics, nearest-series tooltip, color legend, searchable/paginated coverage, sync progress/cancellation/failure details.
- Strategy Lab: relevant parameters only, preflight, costs/risk controls, live metric row, throttled curve, separate trade/decision activity.
- Portfolios: manual ticket, historical inspection, value chart, sortable holdings/trades, adaptive 10-row defaults (11-14 shown in full), sizes through 100.
- Run Library: search, pagination, comparison, detail charts, report export, rerun, and explicit deletion confirmation.
- Tooltips name controls, focus styles support keyboard use, dialogs trap focus natively, navigation supports hash URLs.
- Browser theme and last submitted strategy settings persist locally. Session storage reconnects to background job IDs while the server remains running.
- A browser refresh does not cancel a running backtest. A server restart loses in-memory jobs; successful stored reports remain.
- Background activity uses cursors and bounded buffers. Daily metrics are coalesced rather than appended to the activity stream. Live curves update at most roughly once per 750 ms and use at most 260 streamed points.
- Browser writes reject foreign origins. HTTP request size is bounded. The local server is not an authenticated multi-user deployment.

Report metrics and execution assumptions follow the kinds of inspection workflows available in [TradingView strategy reports](https://www.tradingview.com/support/solutions/43000764138-tradingview-strategy-report-how-to-start/) and [QuantConnect reality modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation).

## Acceptance and Verification

Run the offline unittest suite and compilation checks. Regression coverage must include fees/cost basis, next-open causality, absent/IPO data, pre-start warm-up, negative/invalid inputs, concurrent cash use, cancellation cleanup, snapshots after data refresh, model guards, API progress, and exports.

Performance benchmarks use isolated database copies. Record measured timings and parameters; do not select strategy defaults by maximizing the evaluation period.

Browser visual verification across desktop/mobile remains necessary before calling the UX fully verified. See docs/validation.md for actual checks and remaining limitations.

## Remaining Work

- Point-in-time constituents, delisting outcomes, historical sector classifications.
- Broker-accurate corporate actions, liquidity/market impact, trading calendars, paper/live adapters.
- Immutable raw-data archives, experiment-search accounting, untouched forward/paper neural trials, and point-in-time sector/constituent data.
- Persisted/restartable background jobs and distributed worker coordination.
- Production gateway/MFA policy verification, security review, deployment and restore drills.

## Invited Multi-User Hosting

The CLI and local UI remain single-owner tools. The unauthenticated local server
must bind only to loopback. The separate hosted entry point is Uvicorn/Starlette
behind a private Cloudflare Tunnel and a whole-hostname Access policy.

Authentication is delegated to a managed IdP and Access, with required MFA. The
origin independently verifies signed application JWTs and requires an operator-
provisioned immutable subject. No signup/password/recovery implementation is added
to the app. Operators send individual invitations through their chosen channel.

Every hosted user receives a separate SQLite workspace and job stores. All legacy
services operate within that workspace, including reports, exports and deletion.
No local owner's portfolio data is automatically migrated. Optional market-only
seeding is allowlisted, read-only at source, and requires an empty destination.

Required controls include exact-origin/custom-header mutation checks, secure
response headers, fail-closed authentication, private files, generic errors,
structured audit events, bounded requests/compute, account disable, verified
backups and a one-process lock. Hosted execution remains paper-only; online
discovery is disabled. Restartable queues, horizontal scaling and real brokerage
execution are not implemented. MFA and network isolation are deployment controls
that tests of this repository alone cannot prove.

See [hosting runbook](docs/hosting.md) and [security threat model](docs/security.md)
for launch prerequisites and the additional brokerage security gate.
