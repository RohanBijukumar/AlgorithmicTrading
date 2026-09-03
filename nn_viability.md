# Neural Training and Real-World Viability

Updated 2026-09-07 (local time). The active strategies now use a trained walk-forward registry, not the old v1 weights.

## What Is Implemented

- **31 annual folds, 1996-2026**, with three fixed-seed networks per fold: 93 pretrained networks.
- **295,718 dated samples from 526 symbols**, using locally cached history from 1990 through the available 2026 observations. The training input is a consistent temporary SQLite snapshot.
- Coverage includes the dot-com decline, 2008 financial crisis, subsequent expansions, COVID disruption, and recent market regimes. Older folds have fewer surviving symbols and cannot learn from later recessions.
- Every model is a small 7-input, 12-hidden, 2-output network. Inputs are normalized returns, volatility, RSI, drawdown, and volume trend, without ticker IDs.
- The stock head predicts forward market-relative return. The sector head predicts the sector's average relative return; sector rotation then groups scores by sector.
- Backtests use pure-Python inference only. Training uses PyTorch separately and is never invoked by a backtest.

The registry is [walk_forward_v2.json](algotrading/models/walk_forward_v2.json). It records code/data hashes, input schema, seeds, normalization, split dates, label endpoints, chosen epochs, validation losses, and held-out prediction losses.

## What Blinded Cross-Validation Means Here

For a test year Y:

| Partition | Permitted data | Role |
| --- | --- | --- |
| Training | Feature dates in earlier years; all outcomes end before January 1 of Y-1 | Fit weights and normalization |
| Validation | Features in Y-1; all outcomes end before January 1 of Y | Early stopping only |
| Test | Features in Y with outcomes fully observed within Y and the data cutoff | Score after fitting; never choose weights or epochs |

All stocks on the same date stay in the same partition. Training/validation samples whose forward labels cross a boundary are purged. There is no random stock-row train/test split. The test partition is not passed to the fitting function. A regression test changes test features and targets drastically and verifies that fitted weights remain identical.

Targets enter at the next session's adjusted open and exit at the 21st following session's adjusted close. Missing sessions invalidate the sample; IPO histories are not backfilled. The target is return relative to SPY, with a same-date cross-sectional average fallback before SPY history is available. The stock target is clipped to +/-50% and scaled by 10 during training. This is a prediction target, not a net strategy return: portfolio simulation separately applies fees and slippage.

Each fold's ensemble mixes expanding, trailing 10-year, and trailing 5-year training histories with seeds 42, 43, and 44. Available earlier history is used when a window is shorter than its nominal length. The previous year is reserved for validation in every member. Sample dates are spaced ten observed sessions apart; they are not independent observations, so row count is not an effective independent sample size.

Chronological splits are necessary when future observations must not influence earlier predictions. A generic time split alone does not account for variable forward label endpoints; this implementation explicitly checks them. [scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)

## Regularization

The fixed training recipe uses AdamW weight decay 0.03, dropout 0.1, gradient clipping at 1.0, at most 40 epochs, and validation early stopping with patience 8. Features are standardized using training rows only, with scale floors and clipped normalized inputs. Training loss weights calendar years equally so dense modern history does not simply dominate older years.

AdamW applies decoupled weight decay. These settings are constraints, not proof of profitability or immunity to overfitting. [PyTorch AdamW](https://docs.pytorch.org/docs/stable/generated/torch.optim.AdamW)

Seeds, history windows, architecture, and regularization were fixed before viewing fold results. No search selected the most profitable fold or seed. The final pass includes the expanded top-500 data and uses the same fixed recipe as the initial smaller-data pass. Every eligible fold is retained, not just favorable ones.

## Backtesting Any Window

The blanket requirement to start after 2023 has been removed. A 2008 simulation uses the 2008 ensemble; a 2020 simulation uses the 2020 ensemble. A run crossing New Year changes to the next eligible annual ensemble and reports it in live activity. The entire registry is pinned for each run, so a concurrent offline retraining cannot change its models halfway through.

When no model exists for a year, there are no neural selections. Existing positions rebalance toward cash at the next scheduled decision. A warning identifies missing years. The engine never substitutes a model trained on later outcomes just to produce trades. Missing price data and indicator warm-up remain real requirements.

The shipped coverage is 1996-2026. Tests outside it are allowed, but adding meaningful neural coverage requires sufficient earlier price history and offline training for those years. A future year must not be trained using unrealized labels.

In a multi-year walk-forward replay, an earlier test year may become historical training/validation data for a later fold. That is intentional chronological retraining, not a claim that one fixed model stayed blinded to the entire multi-year run. Later-fold results never change the earlier fold.

## Viability Verdict

**Improved research methodology; live viability remains unestablished.** More folds do not guarantee that a model cannot overfit. Ticker anonymity also does not remove correlated market exposure or selection bias.

An execution/accounting smoke test used the final registry, current @top100 membership, $10,000, and 2007-2009 with default $1 commissions and 5 bps slippage. All three strategies completed and reconciled; runtimes were approximately 3.0-3.6 seconds (excluding the database copy). These are development results, not an untouched performance study:

| Strategy | Net return | Maximum drawdown |
| --- | ---: | ---: |
| NN pattern | -0.16% | -62.79% |
| NN sector rotation | +36.17% | -58.54% |
| NN risk adjusted | -6.94% | -43.12% |

The deep drawdowns are a concrete reason not to equate cross-validation with a safe or profitable live strategy. The name "risk adjusted" describes a scoring penalty, not a guaranteed risk limit.

Important remaining limits:

- The universe contains today's survivors, not point-in-time constituents with delisting outcomes. Current `@topN` membership uses today's caps and can create hindsight bias in old windows.
- Sector classifications are static. Vendor-adjusted prices and symbol histories can be revised; they are not a historical security master or broker corporate-action ledger.
- These models were built today as historical *as-if* experiments. The fact that their inputs precede each fold does not mean this algorithm actually existed then.
- Adjacent folds and stock returns are correlated. Repeatedly changing the protocol after inspecting results still overfits model selection.
- Prediction loss does not establish net trading alpha. Return concentration, beta/sector exposure, costs, liquidity, market impact, and drawdowns still matter.
- The simulator has no live broker adapter, liquidity participation limits, production risk controls, or live reconciliation.

Before promotion, compare against SPY, exposure-matched/equal-weight portfolios, simple momentum, and a regularized linear baseline. Evaluate fixed windows, seeds, turnover, drawdown, cost/delay sensitivity, and modest parameter perturbations. Keep a complete record of attempted variants and account for multiple testing; a deflated Sharpe ratio is one possible method, not yet implemented here. [Bailey and Lopez de Prado: The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)

Then freeze the procedure and run a genuinely unseen forward/paper trial with timestamped predictions and predefined review/risk limits. Do not restart the trial until it happens to look favorable. Published financial ML findings do not establish that this project's small OHLCV model reproduces them. [Gu, Kelly, and Xiu](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/Empirical-Asset-Pricing-in-Machine-Learning.pdf?sc_lang=en)

## Reproduce

The trained registry is bundled; only retraining requires PyTorch:

```bash
python3 -m pip install -e '.[training]'
python3 -m algotrading model train --from 1990-01-01 --to 2026-09-06 --first-test-year 1996 --last-test-year 2026 --epochs 40
python3 -m algotrading model audit
python3 -m algotrading backtest run nn-pattern --symbols MSFT AAPL JPM XOM JNJ PG --from 2007-01-01 --to 2009-12-31 --cash 10000
python3 -m unittest discover -s tests
```

Training does not download prices or write portfolios. Reproducing the exact artifact requires identical input history, code, framework, and settings; timestamps differ between exports. Retain the input database for exact sample reconstruction because refreshing Yahoo data can change history. Training code hashes and dataset fingerprints verify identity but do not replace a raw-data archive.

The earlier [24-run evaluation](docs/neural_evaluation.json) applies to the retired **v1** weights and the previous database snapshot, not these new ensembles. It is retained as historical evidence rather than relabeled. To evaluate the active registry without overwriting that record:

```bash
python3 tools/evaluate_neural.py --db data/trading_simulator.sqlite3 --output data/neural_evaluation_v2.json
```

The old weight files are shown under `legacy_unused` in `model audit`. Their missing training provenance is not repaired retroactively; active strategies no longer call them.
