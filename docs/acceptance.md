# Implementation acceptance — 2026-10-05

The fifteen-task implementation is delivered on the local `codex/futures-bot` branch. It defaults to PAPER with entries paused, 16 USDT virtual capital (an accounting estimate for PHP 1,000), immutable 14.40 USDT floor, and a PHP 100 equivalent total trial-loss allowance. This document records functional implementation evidence, not a profitable strategy or permission to trade or deploy.

## Functional verification

The final verification passed: locked dependency sync; Ruff; mypy across 47 source files; and the full pytest suite, **165 passed, 1 skipped in 152.28 seconds**. This includes 159 unit/integration cases and six authenticated Chromium browser cases. The opt-in real demo test was skipped. One upstream Starlette/httpx test-client deprecation warning remains. Documented CLI config validation, empty-state paper creation, backup and checksum-verified isolated restore also passed; the restored state was `PAUSED_RECONCILIATION_REQUIRED`.

The locked Python 3.12 environment, Ruff, mypy, unit/integration tests, and authenticated Chromium dashboard tests are the normal verification commands in the plan. The suite covers the shared strategy/risk engine, exact Decimal accounting, native protection intent lifecycle, ambiguous submissions, provisional protection before REST, recovery, loss halts, paper interruptions, idempotent controls, authentication, backup/restore, and reports. The browser matrix exercises PAPER/DEMO/LIVE display at desktop and narrow widths, pending controls, escaped messages, expired sessions, and private access. DEMO/LIVE browser cases use local fixtures and submit no venue orders.

Public-network checks observed Binance depth and mark-price streams through the pinned SDK, server time and candle warm-up, and a fully composed PAPER worker with the authenticated dashboard. The composed smoke check reported PAUSED/OBSERVING, fresh worker/stream/account state, no browser console errors, zero private mutations, and graceful shutdown. Generated screenshots, SQLite databases and runtime evidence remain in ignored `artifacts/` and `reports/` directories.

Docker Desktop with WSL2 was installed during this implementation. The final `crypto-bot:verification` image built successfully and started with an isolated Compose acceptance project. It runs as UID/GID 10001 with a read-only root filesystem and publishes only `127.0.0.1:8768` for this check. Its noneditable installed package produces the same code hash as the tested source: `f7fce20d2a237396e79157edf1fbb4f7c4287295c0b2a51c986879f3f760efeb`. Authenticated browser acceptance passed with no console errors, fresh inputs, and PAPER/PAUSED/OBSERVING. Container backup and checksum-verified restore passed. Restart kept the immutable 16 USDT baseline and 14.40 USDT floor, started paused, and permanently interrupted forward qualification as designed. No intents, fills or positions were created.

The initial container clock differed from Binance by about 1.6 seconds, and the clock guard interrupted that session. After the operator synced Windows time, the measured difference was about 100 ms. The interrupted run was preserved and a separate new session was used for healthy acceptance. A resource snapshot showed 81.42 MiB memory and 7.79% CPU; this is a short snapshot, not the required 72-hour measurement. The acceptance containers were stopped after verification; their bind-mounted evidence remains in `artifacts/docker-smoke/`. The temporary acceptance password was removed.

Build, suite, browser, clock, hash, backup/restore and restart logs are in `artifacts/verification/`. These ignored artifacts remain in the implementation checkout. The checkout contains no committed credentials or account state.

## Current release evidence

| Gate | Current evidence | Release consequence |
| --- | --- | --- |
| G1 — logic/accounting | Functional tests verify the implemented logic and invariants | PASS in the generated, hash-bound local evidence |
| G2 — execution/recovery | Fake-exchange lifecycle, restart, unknown submission, loss halt and durable control tests pass | PASS for tested behavior; actual venue behavior is G4 |
| G3 — historical base and stress | A tiny actual public sample produces zero trades and `INSUFFICIENT_WARMUP` | NOT_YET_OBSERVED; no qualifying 24-month history or 50 closed holdout trades in either case |
| G4 — real demo lifecycle | Opt-in suite supplied, skipped in normal verification; no demo orders submitted | NOT_YET_OBSERVED; requires actual lifecycle, partial/zero-fill and private reconnect evidence |
| G5 — forward PAPER | Short functional smoke only; shutdown permanently interrupts that run | NOT_YET_OBSERVED; requires 30 uninterrupted days and 20 closed trades with positive net P&L |
| G6 — operations/economics | Native and Docker auth, backup/restore, startup and restart verified | NOT_YET_OBSERVED; requires 72-hour resource evidence, account/capital feasibility and positive after-hosting economics |
| G7 — operator authorization | No live trial armed or resumed | NOT_YET_OBSERVED; explicit acknowledgement and authenticated Resume are separate final actions |

`docs/evidence-template.json` is deliberately empty evidence: unset hashes fail binding and absent observations cannot pass gates. Copy it into private state, bind `config`, `strategy`, `code`, and `data` hashes and attach actual evidence before evaluating. Editing booleans is not a substitute for performing the checks. Material code/config/capital/data changes invalidate the corresponding results.

`reports/acceptance/evidence.json` records the performed functional checks and current hashes, with their artifact paths. Evaluation in `reports/acceptance/gates.json` gives G1/G2 PASS and G3–G7 NOT_YET_OBSERVED. The unqualified public sample supplies a real data binding for these functional checks; it supplies no qualifying historical, forward, demo or economic evidence.

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
5. Leave actual Binance matching, conditional execution, private reconnect and account eligibility unqualified because no authorized real execution/account evidence was available. Cost if wrong: collect actual demo/account evidence before G4/G6 can pass.
6. Leave profitability, 30-day PAPER and 72-hour operations unqualified because the required data and elapsed observation were unavailable. Cost if wrong: complete the historical, forward and operational evidence before LIVE.
7. Accept local Docker build/start only after running the installed image and authenticated checks on this host. Cost if wrong: repeat acceptance on the target VPS; local acceptance establishes no VPS operating result.
8. Retain the binding specification's conservative minute-OHLC replay assumptions. Cost if wrong: intraminute fills can differ; actual demo/forward evidence is required to assess execution realism.

## Final review and fixes

A single fresh final reviewer examined the completed implementation at `cfd5693`, reporting one critical and four important findings, with no minor findings. All five were accepted and addressed in one TDD fix pass. The reviewer did not perform a second review of the fixes; the author verified them with failing regressions followed by the final green full suite.

| Finding | Resulting behavior | Regression evidence |
| --- | --- | --- |
| C1 — lost ACK followed by terminal private fill | Reconstruct the owned position from durable private observations/fills even when REST open orders omits the terminal entry; retain the earliest fill time and submit provisional protection before unavailable REST | `test_terminal_private_update_recovers_owned_fill_absent_from_open_orders`; `test_private_fill_gets_provisional_stop_before_unavailable_rest` |
| I1 — definitive reduce rejection | Record a definitive rejection as REJECTED, retain protection and permit a later close generation after a fresh position snapshot; ambiguous exits remain unretried | `test_definitive_exit_rejection_allows_new_generation_with_protection_retained`; `test_ambiguous_exit_is_not_resubmitted` |
| I2 — installed code binding | Hash the actual installed package and packaged lock/dependency manifest; missing metadata and dependency mismatch fail closed | `test_code_hash_covers_noneditable_package_and_rejects_missing_manifest`; `test_packaged_dependency_mismatch_fails_closed`; actual source/image hash comparison |
| I3 — silent required market source | Require fresh mark/book and timely completed candles for every configured symbol before observation starts; any subsequent required-source gap permanently interrupts PAPER | Parametrized `test_required_source_gap_permanently_interrupts_paper_even_with_other_streams`; `test_startup_readiness_waits_for_all_required_sources`; `test_required_sources_accept_bounded_clock_skew_and_reject_larger_future_times` |
| I4 — research maintenance metadata | Accept a validated fresh bracket/fee export through the documented download CLI, freeze its provenance/checksum into the dataset hash and supply it to replay sizing | `test_documented_download_cli_freezes_supplied_brackets_and_reaches_sizing` |

The bracket CLI regression uses a clearly synthetic fixture to verify ingestion and sizing; it supplies no real account or G3 evidence. The additional clock regression accepts timestamp skew within the existing one-second guard without changing that limit. Deferred minor findings: **none**.

All fifteen implementation tasks and final review fixes are complete on the local branch. Integration remains the operator's choice.
