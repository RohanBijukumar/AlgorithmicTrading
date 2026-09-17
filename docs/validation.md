# Verification Notes

Verified on 2026-09-07. These checks establish specific software behavior, not trading profitability or production readiness.

## Universe and Neural Update

- 76 Python tests passed after the universe/training changes (4.691 seconds in the final recorded run), including exported inference parity with PyTorch.
- Added 1,279 symbols, bringing the active database universe to 1,524. Nasdaq supplied 5,294 usable market-cap listings; arbitrary @topN ranks the active company subset. Verified @top1500 resolves exactly 1,500 entries.
- Long-history download covered 64 cross-sector/ETF series from 1990 where available. Top-500 price sync from 2000 completed for 499 symbols (including already-covered series); SHEL was rejected for inconsistent OHLC on 2007-04-02. No invalid bars were invented or normalized away. There are now 531 symbols with cached prices.
- Final training produced 31 annual folds (1996-2026), 93 networks, and 295,718 samples across 526 symbols. Training source hash matches the registry. Test samples cannot affect weights/early stopping; all training/validation outcome endpoints precede the permitted boundaries.
- New tests cover arbitrary numeric rankings, duplicate classes, stale caps failing closed, preserving deactivations, invalid provider responses, non-round balances (including 10000, 12345.67, and 0.50), year-specific inference, missing-model behavior, and test-data perturbation invariance.
- Frontend DOM checks passed, including unrestricted starting-cash step validation, new controls, and chart geometry. No connected browser was available for screenshots or native HTML validity testing.
- Final-registry @top100 backtests over 2007-2009 with $10,000 completed for all three neural strategies in approximately 3.0-3.6 seconds per run and reconciled. Large drawdowns remain; see nn_viability.md. Benchmark scripts use temporary copies and do not add user portfolios.
- Restarted-server HTTP preflight accepted nn-pattern with @top137, $10,000, and a 2007-2009 window. It resolved exactly 137 current companies, excluded 20 without prices in that historical window, and reported warm-up/membership limitations. The HTML response contains the corrected unrestricted cash step.

The historical checks below describe the previous, smaller-universe/v1-neural revision. They are not updated performance claims for the new models or larger dataset.

## Automated Checks

- `python3 -m unittest discover -s tests -v`: **62 tests passed**, 1.355 seconds in the final recorded run.
- `python3 -m compileall -q algotrading tools tests`: passed.
- `.venv/bin/ruff check algotrading tests tools`: passed.
- Editable package installation with `.venv/bin/python -m pip install --no-deps -e .`: passed.
- `python3 -m algotrading model audit`: both artifacts validated structurally and reported their eight missing provenance fields.

Tests cover next-open fills, costs/cost basis, cash limits, sell proceeds, missing/IPO observations, historical warm-up, future-price causality, hybrid reproducibility, position caps, concurrent manual orders, cancellation/error cleanup, immutable backtest marks, attribution, export consistency, parameter validation, provider exceptions/invalid bars, progress coalescing, API origin checks, model boundaries, and purged chronological partitions.

The original 27-test suite took about 3.14 seconds before the engine changes and 0.82 seconds afterward on this machine. The expanded suite is not directly comparable because it tests more behavior. Timing observations are not formal controlled performance benchmarks.

## Frontend Checks

`tools/test_frontend.mjs` passed against the real HTML, application JavaScript, and vendored Lucide library using Linkedom. It checks unique IDs, valid icons, event targets, text escaping, null metrics, adaptive pagination, holding sorting, and chart geometry at 360, 700, and 1200 pixel container widths. Each tested chart retained five date ticks and finite coordinates.

To repeat in an environment with Node.js and npm:

```bash
npm install --no-save --package-lock=false linkedom
node tools/test_frontend.mjs
```

Node and Linkedom are optional development tools; the application does not require them at runtime. These DOM tests do not execute the complete browser workflow or a real rendering engine.

**Visual/browser limitation:** the available browser automation runtime reported no connected browser. Desktop/mobile screenshots, actual layout clipping, touch hover behavior, and native dialog focus were therefore not verified visually. Real-browser checks remain necessary before calling the UX fully verified.

## HTTP and Persistence

Loopback HTTP smoke checks returned the app, overview, coverage/catalog, and an existing legacy report. The legacy report response was approximately 237 KB in 0.53 seconds. API tests use isolated databases and cover background backtest completion, fractional transaction-cost settings, live values separate from activity messages, preflight without runs, and rejected foreign-origin mutations.

No benchmark or neural-evaluation portfolios were written into the user's source database. Evaluation scripts open it read-only and back it up to a temporary database. Existing run summaries were not rewritten; legacy runs remain visibly distinguished from the new execution model.

## Local Runtime Samples

Cached 2024-01-01 through 2025-12-31 data, $100,000, catalog defaults, $1 commission and 5 bps slippage. Measurements include the backtest call but exclude downloading; they are single-machine samples and may vary.

| Universe | Strategy | Seconds |
| --- | --- | ---: |
| SPY, QQQ, IWM | Buy-and-hold | 0.185 |
| SPY, QQQ, IWM | Dual momentum | 0.198 |
| SPY, QQQ, IWM | Time-series momentum | 0.189 |
| SPY, QQQ, IWM | Inverse volatility | 0.180 |
| Current active universe | Momentum | 1.830 |
| Current active universe | RSI strength | 1.681 |
| Current active universe | NN pattern | 2.597 |
| Current active universe | NN sector rotation | 2.005 |
| Current active universe | NN risk adjusted | 2.414 |

Every listed run reconciled. The main performance changes are a per-run cached tape, in-memory accounting, batched final persistence, linear portfolio-history reconstruction, and bounded/coalesced UI progress. The model feature extractor now processes only its required trailing 64 observations.

Repeat measurements without changing source research history:

```bash
python3 tools/benchmark.py --db data/trading_simulator.sqlite3 --symbols SPY QQQ IWM
python3 tools/benchmark.py --db data/trading_simulator.sqlite3 --symbols @universe --strategies momentum rsi-strength nn-pattern
```

## Neural Evaluation

The independent fixed-window evaluation produced 24 base/stress runs, all accounting-reconciled. See [the assessment](../nn_viability.md) and [full results](neural_evaluation.json). None is certified untouched out-of-sample evidence. No weights or defaults were selected to maximize these results.

## Remaining Risks

- Average-cost, adjusted-price normalized units are not a broker-accurate corporate-action or tax ledger.
- Current universe/sector membership, absent delisting outcomes, missing-session carry-forward, and no liquidity/impact model can bias results.
- Retired v1 neural artifacts lack reproducible provenance. Active walk-forward artifacts record code/sample hashes and label endpoints, but raw point-in-time histories and untouched forward/paper evidence remain missing.
- Background jobs are in-memory, cooperative, and local-only; server crashes/restarts do not resume work.
- The service has no authentication, broker connection, paper/live reconciliation, or live risk controls. Use the loopback host.
- Accounting and regression tests cover known invariants, not every possible data/provider/strategy failure.
# Hosted Security Verification (September 17, 2026)

The hosted release adds offline signed-JWT and ASGI tests for token forgery,
issuer/audience/expiry, signing-key rotation/outages, allowlist checks, account
disable, identity-header spoofing, duplicate assertions, portfolio/report/job
isolation, CSRF, host validation, body/rate/worker bounds, sanitized errors,
single-process enforcement, market-only imports and verified backups.

The full suite was run in the project virtual environment with hosted dependencies.
Two optional PyTorch training tests are skipped in that environment; the new
security tests do run. `tools/test_hosted_browser.py` exercises real Chromium at
1440x1000 and 390x844 with ephemeral signed identities and intercepted ASGI
transport: private accounts, fractional cash, mobile sign-out, expired-session
locking, script compatibility with CSP, and no page-level horizontal overflow.
It does not simulate Cloudflare or prove the external MFA policy.

The production dependency lock passed `pip-audit` with no known vulnerabilities
after upgrading Starlette to 1.6.0. This is an advisory lookup at a point in time,
not a security certification. Docker Compose configuration parses successfully;
the image build and actual Tunnel/IdP deployment were not verified because the
Docker daemon was unavailable and cloud credentials/domain were not configured.

Reproduce the browser check after installing `playwright` and Chromium:

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python tools/test_hosted_browser.py --output-dir /tmp/hosted-browser-check
```

Do not launch publicly before completing the deployment-specific acceptance
checks in [hosting.md](hosting.md) and an independent security review. Never
connect funded brokerage accounts to this release.
