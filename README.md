# AlgorithmicTrading

A local research workspace for historical market data, simulated portfolios, and strategy backtesting. The web UI and CLI share the same SQLite database. No brokerage account, cloud service, or paid API is required. This is a simulator, not a live execution system.

## 1. Start the workspace

Requires Python 3.11 or newer. Run from this directory:

```bash
python3 -m algotrading market init
python3 -m algotrading ui serve
```

Open **http://127.0.0.1:8000**. Stop the server with **Ctrl+C** in its terminal. If the port is occupied, use `--port 8001`. The UI includes a dark/light theme toggle and a short reference panel.

Data is stored in `data/trading_simulator.sqlite3`. Use `python3 -m algotrading --db /path/to/research.sqlite3 ...` to use another database. Existing databases migrate automatically without deleting portfolios or market data.

## 2. Download market history

On an empty database, Overview offers a starter download. Otherwise use **Market > Data sync**. Include at least a year before your intended test window to warm up long-term signals.

CLI alternative:

```bash
python3 -m algotrading market sync --symbols SPY QQQ IWM TLT GLD EFA EEM --from 2023-01-01 --to 2025-12-31
python3 -m algotrading market coverage
```

Yahoo downloads are sequential. Individual failures are reported while the remaining symbols continue; `--fail-fast` restores stop-on-first-error behavior. `--force` refreshes the requested range, not the entire database. It does not delete unrelated data.

Add or deactivate symbols at any time:

```bash
python3 -m algotrading market add-symbol VOO --name "Vanguard S&P 500 ETF" --asset-type etf
python3 -m algotrading market remove-symbol VOO
```

Deactivation retains historical prices and trades. Downloads require an internet connection; cached research works offline.

The bundled universe now includes roughly 1,500 companies, plus market ETFs. Refresh company rankings or expand further from **Market > Symbol universe**:

```bash
python3 -m algotrading market cap-refresh
python3 -m algotrading market expand --count 1500
python3 -m algotrading market sync --symbols @top500 --from 2000-01-01 --to 2025-12-31
```

Market caps come from [Nasdaq's public US-listed stock screener](https://www.nasdaq.com/market-activity/stocks/screener), cached locally for 24 hours. Expansion preserves deactivated symbols and does not automatically download prices. During this update the top 500 were synced, with one rejected provider series (SHEL); other newly added symbols still need a data sync.

## 3. Run a baseline, then a strategy

Open **Strategy Lab**, select a preset, choose dates and capital, and use **Check data** before **Run backtest**. The return panel updates daily, activity shows trades/decisions, and the equity chart updates at a limited rate. Cancel is cooperative; cancelled backtests leave no partial portfolio.

CLI alternatives:

```bash
python3 -m algotrading backtest run buy-and-hold --symbols SPY --from 2024-01-01 --to 2025-12-31 --cash 100000
python3 -m algotrading backtest run dual-momentum --symbols SPY QQQ IWM EFA EEM TLT GLD --from 2024-01-01 --to 2025-12-31 --lookback-days 252 --skip-days 21 --rebalance-days 21 --top-n 3
python3 -m algotrading backtest run inverse-volatility --symbols SPY TLT GLD --from 2024-01-01 --to 2025-12-31 --lookback-days 63
python3 -m algotrading backtest run momentum --symbols @universe --from 2024-01-01 --to 2025-12-31 --max-position-pct 15 --cash-reserve-pct 5 --rebalance-tolerance-pct 0.5
```

Default backtest execution is **next-session adjusted open**, with **$1 commission per fill and 5 basis points of slippage**. Both are configurable. Orders are resized for available cash and position limits; missing execution-session prices produce explicit skipped-order events.

Use `--check-only` to check parameters and coverage without creating a run. A terminal updates daily progress on one line; redirected output includes daily values only with `--daily-progress`.

`@universe` means all active symbols. `@topN` accepts any positive integer, such as `@top25`, `@top100`, or `@top500`, and ranks active companies by their latest cached Nasdaq USD market caps. ETFs, unavailable caps, and known duplicate share classes are excluded. These are **current rankings, not historical constituents**. Use a selector by itself. A stale cache must refresh successfully; an oversized request gives an error rather than silently returning fewer companies.

Starting cash can be any positive balance, including `10000`, `12345.67`, or `0.50`. Very small balances may afford no whole-unit positions.

## 4. Inspect and compare results

**Run Library** opens saved reports with equity/benchmark charts, drawdown, monthly returns, profit attribution, execution assumptions, and data warnings. Select two to four runs with matching test dates to compare them. **Rerun** restores settings; export buttons download CSV equity or a full JSON report.

```bash
python3 -m algotrading backtest list
python3 -m algotrading backtest show 1
python3 -m algotrading backtest export 1 --format csv --output data/run-1.csv
python3 -m algotrading backtest export 1 --format json --output data/run-1.json
```

Existing runs are labeled **Legacy execution**. They retain their historical summaries; rerun them to apply the corrected engine. Newly completed portfolios are read-only and preserve daily marks even if market data is refreshed later.

## 5. Track a manual portfolio

Create a portfolio in **Portfolios**, select it, and use the trade ticket. Manual trades require an exact stored session and chronological entry.

```bash
python3 -m algotrading portfolio create --name demo --cash 100000
python3 -m algotrading portfolio buy demo SPY --shares 10 --date 2024-03-01
python3 -m algotrading portfolio sell demo SPY --shares 5 --date 2024-06-03
python3 -m algotrading portfolio value demo --date 2024-12-31
python3 -m algotrading portfolio audit demo --date 2024-12-31
```

Cash, realized P&L, and cost basis are reconstructed from the ledger. Holdings and trades are sortable and paginated. Deleting a portfolio also deletes its linked reports; deleting only a run summary retains its portfolio.

## Research limitations

Adjusted prices embed corporate actions; share counts are normalized simulation units, not a broker-accurate split/dividend ledger. Current universe membership creates survivorship bias. Missing held prices are carried forward and flagged. There is no liquidity/market-impact model, borrow, taxes, intraday execution, or cash interest. Historical profitability is not guaranteed to persist.

Neural strategies now use pretrained, purged walk-forward ensembles covering 1996-2026. The 2024 start-date restriction is gone. Each year uses three models trained and validated only on earlier outcomes. Missing model years produce no neural selections, with a warning; there is no future-trained fallback. Backtests never retrain models. See [the neural training and viability assessment](nn_viability.md).

## 6. Retrain neural models offline

This is optional: trained artifacts are bundled. To reproduce training or extend fold coverage, install the training extra and sync sufficient older data first:

```bash
python3 -m pip install -e '.[training]'
python3 -m algotrading model train --from 1990-01-01 --to 2026-09-06 --first-test-year 1996 --last-test-year 2026 --epochs 40
python3 -m algotrading model audit
python3 -m algotrading backtest run nn-pattern --symbols MSFT AAPL JPM XOM JNJ PG --from 2007-01-01 --to 2009-12-31 --cash 10000
```

Training takes a consistent temporary database snapshot. It uses date-grouped, label-purged splits; prior-year validation; expanding, 10-year, and 5-year histories; AdamW weight decay; dropout; early stopping; and equal-year training weights. Test outcomes never choose weights or epochs. More folds reduce some evaluation weaknesses but cannot eliminate overfitting, survivorship bias, or repeated-research bias.

## Development and references

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q algotrading
python3 tools/benchmark.py --db data/trading_simulator.sqlite3
```

The benchmark uses an isolated temporary database copy; it does not modify your research history. Runtime has no required Python dependencies. Lucide icons are vendored locally with their license.

- [Command and Python API reference](help.md)
- [Current specification](spec.md)
- [Verification notes](docs/validation.md)
