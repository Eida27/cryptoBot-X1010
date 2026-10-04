# Implementation acceptance — 2026-10-05

The fifteen-task implementation is delivered on the local `codex/futures-bot` branch. It defaults to PAPER with entries paused, 16 USDT virtual capital (an accounting estimate for PHP 1,000), immutable 14.40 USDT floor, and a PHP 100 equivalent total trial-loss allowance. This document records functional implementation evidence, not a profitable strategy or permission to trade or deploy.

## Functional verification

The final task verification passed: locked dependency sync; Ruff; mypy across 47 source files; 147 unit/integration tests; and six Chromium browser cases. The opted-in demo test was skipped. One upstream Starlette/httpx test-client deprecation warning remains. Documented CLI config validation, empty-state paper creation, backup and checksum-verified isolated restore also passed; the restored state was `PAUSED_RECONCILIATION_REQUIRED`.

The locked Python 3.12 environment, Ruff, mypy, unit/integration tests, and authenticated Chromium dashboard tests are the normal verification commands in the plan. The suite covers the shared strategy/risk engine, exact Decimal accounting, native protection intent lifecycle, ambiguous submissions, provisional protection before REST, recovery, loss halts, paper interruptions, idempotent controls, authentication, backup/restore, and reports. The browser matrix exercises PAPER/DEMO/LIVE display at desktop and narrow widths, pending controls, escaped messages, expired sessions, and private access. DEMO/LIVE browser cases use local fixtures and submit no venue orders.

Public-network checks observed Binance depth and mark-price streams through the pinned SDK, server time and candle warm-up, and a fully composed PAPER worker with the authenticated dashboard. The composed smoke check reported PAUSED/OBSERVING, fresh worker/stream/account state, no browser console errors, zero private mutations, and graceful shutdown. Generated screenshots, SQLite databases and runtime evidence remain in ignored `artifacts/` and `reports/` directories.

Docker is not installed on this host. Both `docker compose config` and `docker build -t crypto-bot:verification .` were attempted and could not run. The digest-pinned, non-root image and loopback Compose configuration are supplied; container build/start acceptance is unverified.

## Current release evidence

| Gate | Current evidence | Release consequence |
| --- | --- | --- |
| G1 — logic/accounting | Functional tests verify the implemented logic and invariants | Can be recorded as PASS for the tested, hash-bound code |
| G2 — execution/recovery | Fake-exchange lifecycle, restart, unknown submission, loss halt and durable control tests pass | Can be recorded as PASS for tested behavior; actual venue behavior is G4 |
| G3 — historical base and stress | A tiny actual public sample produces zero trades and `INSUFFICIENT_WARMUP` | NOT_YET_OBSERVED; no qualifying 24-month history or 50 closed holdout trades in either case |
| G4 — real demo lifecycle | Opt-in suite supplied, skipped in normal verification; no demo orders submitted | NOT_YET_OBSERVED; requires actual lifecycle, partial/zero-fill and private reconnect evidence |
| G5 — forward PAPER | Short functional smoke only; shutdown permanently interrupts that run | NOT_YET_OBSERVED; requires 30 uninterrupted days and 20 closed trades with positive net P&L |
| G6 — operations/economics | Local auth and backup/restore verified | NOT_YET_OBSERVED; requires 72-hour resource evidence, account/capital feasibility and positive after-hosting economics |
| G7 — operator authorization | No live trial armed or resumed | NOT_YET_OBSERVED; explicit acknowledgement and authenticated Resume are separate final actions |

`docs/evidence-template.json` is deliberately empty evidence: unset hashes fail binding and absent observations cannot pass gates. Copy it into private state, bind `config`, `strategy`, `code`, and `data` hashes and attach actual evidence before evaluating. Editing booleans is not a substitute for performing the checks. Material code/config/capital/data changes invalidate the corresponding results.

The actual sample in `data/public-sample` covers three minutes of SOLUSDT on 2026-10-02. Base/stress JSON, CSV and HTML outputs in `reports/public-sample` use the same frozen chronological split boundaries and report the missing warm-up. They do not establish capital feasibility, returns, trade frequency or the long-term suitability of the strategy.

No recurring hosting service has been purchased or deployed; actual hosting spend is zero. Reports support explicit 30-day proration and USD/USDT assumptions. A 6 USD/month scenario is an example, not a quote. Peak RSS, average CPU and p99 loop lag over 72 hours have not been measured, and no economically justified VPS choice is established.

## Reproduce and continue locally

Run from the implementation checkout:

```powershell
uv sync --locked --group dev
uv run ruff check .
uv run mypy src/crypto_bot
uv run pytest tests/unit tests/integration -q
uv run playwright install chromium
uv run pytest tests/browser -q
uv run cbot config validate --config config/paper.toml
uv run cbot auth set-password --out .env
uv run cbot serve --config config/paper.toml
```

Open `http://127.0.0.1:8000` and authenticate with the locally set password. The service starts paused. PAPER cannot place venue orders. Without actual maintenance metadata or complete/fresh market inputs it records an entry veto. Use the [local runbook](../runbooks/local.md) for expiring, read-only maintenance-bracket exports, CLI controls, an explicit new paper session and mode isolation.

Use [demo instructions](../tests/demo/README.md) only after separate authorization for real demo mutations. Start sustained VPS paper observation only after deployment is separately authorized. Account eligibility and fee/bracket feasibility require actual account evidence. Keep all credentials and runtime/account data outside Git. Preserve prior interrupted runs rather than inventing offline observations.

## Recorded implementation decisions

1. Preserve the user-supplied plan/spec paths and valid links. Cost if wrong: both documents need a coordinated move.
2. Use a native managed worktree and execute tasks sequentially. Cost if wrong: integrating the local branch remains a separate user choice.
3. Use the common SDK raw transport because generated generic wrappers fail on Python 3.12 and model coercion can lose Decimal precision. SDK signing/session behavior is retained and tested at the HTTP boundary. Cost if wrong: revise the adapter shim; G4 blocks LIVE until actual demo evidence exists.
4. Reject historical funding schedule changes without verified interval history. Missing settlements must not appear complete. Cost if wrong: add authoritative interval-change metadata before qualifying that dataset.

The final review and any deferred findings are recorded here before handoff. The local branch remains available for review and integration.
