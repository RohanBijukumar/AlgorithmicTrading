# Reconstructed Development History

This Git history was organized on September 17, 2026, from the project's existing working tree and development conversation at the owner's request. Dates from August 3 through September 17 are intentionally reconstructed dates, not independently recorded development timestamps. Both author and committer dates use the same reconstructed time.

The original June 22 README-only commit is preserved unchanged as a repository placeholder. The reconstructed implementation timeline starts in early August. Commits use the repository's configured author identity; no additional contributors are invented.

## Development Story

| Date (Pacific) | Milestone |
| --- | --- |
| August 3-7 | Local storage, provider caching, editable universe, and sync regression coverage |
| August 8-12 | Portfolio accounting, CLI, backtesting, and reinvestment of rotation proceeds |
| August 13-20 | Web workspace, pagination iterations, chart readability, deletion and sync checks |
| August 21-25 | Availability-aware replay, hybrid and neural strategy checks, bounded discovery |
| August 26-30 | Live metrics separated from activity, chart throttling, execution and ledger safeguards |
| September 1-7 | Neural evaluation, broader coverage, current-cap selectors, purged training |
| September 9-17 | Arbitrary cash inputs, model boundary checks, benchmarks and documentation |

These commits are a reconstruction, not recovered historical versions. Feature commits introduce slices of the final implementation. Test commits progressively add existing regression cases corresponding to issues raised in the conversation. Selected fix commits restore final behavior from deliberately reconstructed predecessor variants: idle rebalance cash, 50-row pagination, chart labels, daily-return activity noise, chart refresh frequency, and the starting-cash input constraint. Those predecessor variants illustrate the reported problems; they are not evidence of the exact original source code.

Every reconstructed commit is marked in its message and contains a real tree change. Shared modules can include later capabilities before their dedicated regression commits. Some earlier commits refer to modules introduced by later slices; not every intermediate checkout is an independently runnable release. Only the final tree is the supported, verified state. Model artifacts and research outputs retain their actual embedded provenance and timestamps. They are not backdated to match commit dates.

Discovery is bounded price-data research, not a historical news/LLM research service. Current-cap selectors do not remove survivorship bias. Neural validation and historical backtests do not establish future profitability.

The expanded reconstruction replaces the ten earlier local milestone commits. Their tip is preserved at `history/milestones-backup-2026-09-17`. The original root and remote-tracking history are unchanged; nothing is pushed. Application files are byte-for-byte unchanged at the final tip; only this history document changes. Private local market/portfolio databases, virtual environments, build outputs, and caches are excluded.

Inspect the story with:

```bash
git log --reverse --format='%h %ad %s' --date=short
git log --stat
```
