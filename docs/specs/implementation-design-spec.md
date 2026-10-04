# Crypto Futures Bot — Implementation Design Specification

**Date:** 2026-10-04  
**Version:** 1.0  
**Companion:** [implementation-plan.md](../plans/implementation-plan.md)  
**Purpose:** Build instructions for a new, single-user project. No existing repository was supplied or inspected. This document does not report an implemented bot, verified account eligibility, backtest results, or authorization to place live orders.

## 1. Agreed brief and decision record

Build a cost-conscious personal trading bot, initially on a PC that is available only during some hours. Start with reproducible rule-based research, then local paper trading, continuous VPS paper trading, and a separately enabled small live trial. The user selected a custom Python application with a basic browser dashboard.

| Decision | Agreed requirement |
|---|---|
| Exchange | Binance, conditional on the user's actual Futures API eligibility |
| Instrument | USDT-margined perpetual futures |
| Initial trading capital | PHP 1,000, excluding hosting costs |
| Total trial-loss threshold | PHP 100 equivalent, measured from initial trial equity |
| Strategy | Completed 1-hour candle breakout, filtered by EMA(200) |
| Long signal | Close above EMA(200) and the preceding 20 candles' highest high |
| Short signal | Close below EMA(200) and the preceding 20 candles' lowest low |
| Initial stop | Two times signal-candle ATR(14) from actual entry |
| Profit target | Twice the initial stop distance, before costs |
| Maximum holding time | 48 hours |
| Planned risk per trade | At most 1% of current equity, capped at the initial PHP 10 equivalent, including cost allowances |
| Positions | At most one open position across the entire bot |
| Margin and leverage | Isolated; leverage setting at most 2x; automatic margin replenishment disabled |
| Position adjustments | No averaging down, increasing stakes after losses, or widening an established stop |
| Loss shutdown | Block entries, cancel entry orders, attempt to flatten, persist a halt for manual review |
| Interface | Basic private browser dashboard |
| Architecture | Custom Python service, official Binance Python SDK, SQLite, FastAPI and HTML |
| Hosting | Target a small VPS after measurement; no deployment or purchase in this task |

**Engineering defaults:** The numerical freshness, liquidity, exposure, simulation and evidence thresholds below complete the implementation brief. They are initial design choices, not claims of optimal trading parameters or separately stated user preferences. They must be visible in configuration and recorded in every run. The agreed financial ceilings cannot be raised through dashboard controls.

A profitable strategy has not been established. A valid outcome is a functioning research system that concludes this balance or strategy is unsuitable for live deployment.

## 2. Scope and release boundaries

Version 1 supports one operator, one Binance Futures wallet, a fixed research universe, one strategy, and one position. Reuse the SDK for authenticated transport and signing; own the business rules, accounting, reconciliation and execution lifecycle.

Include historical data ingestion, deterministic backtesting, paper execution, demo integration checks, a gated live adapter, reports, dashboard, restart recovery, deployment instructions and backups.

Do not add machine learning, LLM calls, sentiment feeds, paid data, automated parameter optimization, grid/martingale trading, copy trading, multiple exchanges, account transfers, withdrawal functions, public user registration, mobile apps or a separate JavaScript frontend build.

No production trading credentials are needed for historical research or local paper mode. The software must not create accounts, buy hosting, fund wallets, activate live trading, or execute orders merely because an implementation task or test suite was run.

## 3. Architecture and runtime

### 3.1 Stack

- Python 3.12; manage and lock dependencies with uv.
- Binance-maintained package `binance-sdk-derivatives-trading-usds-futures` behind a narrow adapter. Verify the current compatible release and supported methods during implementation, then pin it in `uv.lock`.
- FastAPI, Uvicorn, Jinja2 and pydantic-settings; server-rendered HTML, local CSS and small vanilla JavaScript files.
- SQLite with explicit SQL migrations, WAL, foreign keys, a 5-second busy timeout and FULL synchronous durability.
- Python `Decimal` with precision 34 for money, quantity, filters and order prices; accept decimal strings at boundaries. Store monetary values as decimal text, never SQLite REAL. Indicator calculations use the same deterministic Decimal routines in research and execution.
- pytest, pytest-asyncio, Hypothesis, Ruff and mypy for development. Browser verification uses Playwright only as a development dependency.
- Docker Compose for a consistent Linux runtime on the PC and VPS; document Windows with Docker Desktop/WSL2 as well as native Linux. Do not require a Windows host.
- Pin dependency versions and the container image digest after compatibility checks. No floating production image tags.

### 3.2 Components

~~~mermaid
flowchart TD
    Binance["Binance data and orders"] <--> Adapter["Exchange adapter"]
    Adapter <--> Worker["Single bot worker"]
    Rules["Strategy and risk rules"] --> Worker
    Worker <--> Store["SQLite state and journal"]
    Dashboard["Private dashboard"] --> Queue["Durable control commands"]
    Queue --> Worker
    Store --> Dashboard
    History["Historical data"] --> Simulator["Backtest and paper execution"]
    Rules --> Simulator
~~~

One service process runs one Uvicorn worker and one asynchronous bot worker. A database-path OS lock prevents a second process from operating the same state. Reserve at most one entry/position slot in a SQLite transaction. No overlapping schedulers, multiple replicas or hot-reload in operational modes.

The worker owns exchange mutations. HTTP handlers authenticate, validate and enqueue commands; they never submit orders. SDK I/O must not block the event loop. If the SDK is synchronous, use a bounded executor and retain ownership of an in-flight request even when its caller times out.

Heavy history downloads and backtests run as separate CLI jobs while the operational service is stopped. Simulator runs use separate databases and output directories. Dashboard reads use their own read connections. Private data remains on the host.

### 3.3 Core responsibilities

| Module group | Responsibility |
|---|---|
| Domain | Typed candles, snapshots, signals, filters, orders, fills and enums |
| Strategy | Indicators and entry signals; no exchange calls or position sizing |
| Risk | Equity, budgets, quantity rounding, exposure, trial halt and preflight vetoes |
| Exchange | SDK mapping, environment selection, streams, rate limits and normalized errors |
| Execution | Durable intents, entry/protection/exit orchestration and recovery |
| Storage | Transactions, idempotent event ingestion, command queue and migrations |
| Research | Data validation, conservative execution simulation, reports and evidence gates |
| Dashboard | Authenticated views and clearly separated operating controls |

## 4. Modes, account ownership and live enablement

| Mode | Market source | Order destination | Allowed host |
|---|---|---|---|
| BACKTEST | Frozen historical files | Deterministic simulator | PC |
| PAPER | Production public Futures data | Local simulator | PC or VPS |
| DEMO | Binance non-production environment | Demo adapter only | PC or VPS |
| LIVE | Production data and private account state | Production adapter | Continuously operated VPS |

Default to PAPER with entries paused. Mode changes require service restart and a different mode-specific database. Never copy demo credentials into live configuration. Do not allow arbitrary endpoint URLs from a dashboard or query parameter.

For the documented API revision, production REST is `https://fapi.binance.com` and demo REST is `https://demo-fapi.binance.com`. Confirm the current official environment map before enabling either private adapter. SDK/environment mismatches fail startup; public production data does not imply demo order routing. [S1]

Live eligibility requires verified access for this account, the selected products, and the deployment region. Binance's Philippine site describes a BlockShoals/SEC sandbox arrangement; this is not evidence that this particular account has Futures API access. Do not use a VPS, VPN or alternate endpoint to evade restrictions. [S7]

Use a Futures wallet reserved exclusively for this bot during a trial. Other spot holdings are outside scope. Require One-way Mode, Single-Asset Mode, isolated positions and automatic margin replenishment off. Preflight checks settings; it must not silently alter account-wide modes. A dedicated subaccount is optional, not a prerequisite.

Check for external orders, positions, deposits, withdrawals, transfers and non-USDT fee payments. Unexpected activity blocks entries and requires reconciliation; do not adopt unknown positions or treat deposits as profit. The normal configuration disables BNB fee payment. If unsupported fee assets occur, preserve raw evidence and halt new entries until their accounting is resolved.

Live arming requires all of:

1. Passing acceptance evidence bound to the code, configuration, data and strategy hashes.
2. A clean account/settings preflight and symbol feasibility report. G1 through G6 must already pass; G7 is recorded by this arming/review flow and is not required to have passed before arming.
3. `MODE=LIVE`, `HOST_PROFILE=vps` and `LIVE_TRADING_ENABLED=true`.
4. A local CLI arming action that records the operator's acknowledgement and an immutable trial baseline.
5. An authenticated dashboard Resume action after successful reconciliation.

These are technical gates, not proof of legal eligibility or profitability. A configuration flag alone cannot certify host uptime.

## 5. Trial accounting and risk

### 5.1 Immutable baseline

Let `B` be the operator-declared net USDT allocation corresponding to the PHP 1,000 budget after conversion and funding, with no open positions. Require an explicit `initial_capital_usdt` and record the conversion assumption or actual funding reference. Never infer permission to trade a larger allocation from the account's balance. At arming, require the exclusive Futures wallet to match the declared allocation within 0.01 USDT; otherwise refuse arming for review. Record the user's PHP 1,000 reference and display conversion `php_per_usdt = 1000 / B`. This fixed display conversion is not a current FX quote. A sample local paper configuration may use 16.00 virtual USDT, clearly labeled as an assumption; evidence for a live trial must be regenerated for its declared allocation.

Set the loss floor `F = 0.90 × B`. The initial PHP 10 equivalent is `0.01 × B`. PHP exchange-rate movements do not change these limits.

Current mark equity `E = wallet_balance_usdt + unrealized_pnl_usdt`. Wallet balance already includes booked commissions and funding; do not subtract them again. Maintain a separate event ledger for explanation and reconciliation.

For the shutdown check, conservatively estimate equity after closing:

~~~text
E_guard = E - estimated_unpaid_exit_fee - exit_slippage_allowance
shutdown when E_guard <= F
~~~

The dashboard shows both mark equity and estimated closing equity. This can trigger slightly before displayed mark equity reaches the PHP 900 equivalent. Hosting is reported separately and is not deducted from the Futures wallet for this check.

The floor is relative to starting equity, not a trailing peak or a daily reset. Restarts, midnight, gains, dashboard Resume and new sessions do not reset it. A loss halt cannot be cleared by ordinary Resume. Starting a genuinely new live trial requires a flat/reconciled wallet and a new explicit CLI acknowledgement; archive the old trial intact.

### 5.2 Planned trade risk

At a flat, healthy entry decision, use:

~~~text
R = min(0.01 × E, 0.01 × B, max(E - F, 0))
d = abs(reference_entry - rounded_stop) / reference_entry
c = entry_fee_rate + exit_fee_rate
    + entry_slippage_reserve + exit_slippage_reserve
    + adverse_funding_reserve
notional_by_risk = R / (d + c)
collateral_capacity = available_balance / (1 / leverage + c)
maximum_notional = min(notional_by_risk, E, collateral_capacity, leverage_bracket_limit)
quantity = floor_to_step(maximum_notional / reference_entry)
~~~

The `E` notional ceiling is an additional engineering exposure limit. At the default 2x exchange leverage setting it normally uses at most half the equity as initial margin; the remaining funds are a reserve. Leverage is fixed at 2x by default and never raised to satisfy minimum orders.

Reserve available collateral for initial margin plus planned entry/exit costs. Read actual leverage brackets and available balance; do not infer free funds from wallet balance alone.

Fee model: use the larger of the verified account taker commission and `0.0006` per execution. For research without account access, `0.0006` is an explicit modeling assumption, not a claim about Binance's fee schedule. Defaults reserve `0.0005` of entry notional for entry slippage and `0.0010` for exit slippage. Entry reference uses the executable ask for a long and bid for a short; spread is already included there.

Adverse funding reserve per settlement is the maximum of the absolute current published rate, the largest absolute rate observed over the preceding seven days, and `0.0001`. Multiply it by `ceil(48 hours / verified funding interval) + 1`. Missing interval/rate/history blocks new entries. Do not assume every contract always funds every eight hours. This is an allowance, not a guaranteed bound on future funding. Re-evaluate on funding updates and before a settlement; if estimated planned loss exceeds the remaining approved budget, close and pause for review.

After rounding, recheck planned loss, available collateral, filters, minimum notional, price bands, quantity steps and liquidation buffer. Require a 20% cushion along the entry-to-liquidation distance: for a long, stop >= liquidation + 0.20 × (entry - liquidation); for a short, stop <= liquidation - 0.20 × (liquidation - entry). The stop must also be on the correct side of entry. Before entry, use a conservative estimate from verified maintenance brackets and intended isolated margin; after fill, verify the exchange-reported liquidation price. Unknown liquidation inputs block entry or trigger protection-failure handling after fill. Never round quantity upward or tighten the strategy's volatility stop to manufacture affordability. Report a reason code when no valid quantity exists.

Position limits, fees and sizing are identical in paper and live logic. Planned loss is not a guaranteed maximum: fills, gaps, funding changes and outages can exceed the allowance.

### 5.3 Shutdown and freshness

Evaluate account risk at least once per second while running or paused with exposure. Consume private events continuously; while exposed, refresh account/positions through REST every 5 seconds. When flat, refresh every 30 seconds and obtain a snapshot no older than 15 seconds before entry.

Block entries when any required input is stale: quote older than 3 seconds, market stream heartbeat older than 10 seconds, account snapshot older than 15 seconds, filters older than 6 hours, or unresolved stream/reconciliation gaps. A missed scheduled funding update also blocks entry. Refresh filters on a filter rejection without increasing size or retrying the same signal.

A loss breach first commits the persistent halt and disables entries, then cancels outstanding entry orders, attempts reduce-only flattening, verifies flatness, and cancels residual protective orders. Retain exchange stops until flatness is confirmed. No new position may open while any old protective order is unresolved.

On connectivity loss, stop entries and show DEGRADED. Maintain or recover protection as access permits; do not pretend a failed flatten request closed the position. Technical halts need manual Resume after successful reconciliation. An OS crash cannot enforce an account-level loss limit; exchange protective orders provide a separate layer, not a guaranteed loss cap.

## 6. Exact strategy and market selection

### 6.1 Universe and entry eligibility

Initial research candidates are `BTCUSDT`, `ETHUSDT` and `SOLUSDT`. This is an engineering default, not a recommendation to purchase any asset. No automatic addition of newly listed or cheaper contracts. Each candidate must be a currently trading, USDT-settled perpetual and satisfy all filters and risk constraints.

For entry require trailing 24-hour quote volume of at least 100,000,000 USDT, current bid/ask spread no greater than 10 basis points, and absolute mark/mid-price divergence no greater than 20 basis points. Reject a quote more than 20 basis points away from the signal close. These thresholds apply prospectively using information available at the decision time.

Evaluate eligible signals in descending preceding-24-hour quote volume; break ties by symbol name. Try the next candidate only when the previous candidate is rejected before any order intent is submitted. Once an entry is attempted, do not attempt another symbol for that same hourly decision.

Exchange minima can make every candidate infeasible at this balance. The correct result is `NO_AFFORDABLE_TRADE` and continued observation. Live release requires demonstrated feasible trades in the chosen universe. Do not require BTC or ETH to pass, or silently replace them with speculative contracts. The current venue filters, not figures in this document, are authoritative. [S2]

### 6.2 Indicator definitions

Normalize candle intervals to UTC with an exclusive close timestamp. Only final candles whose close is at or before verified exchange time may affect a signal. Signal prices and ATR use contract-trade candles; mark-price data is reserved for equity and protective-trigger modeling.

- EMA(200): seed with the arithmetic mean of the first 200 closes; thereafter `ema = close × (2/201) + previous_ema × (199/201)`.
- True range: `max(high-low, abs(high-previous_close), abs(low-previous_close))`.
- ATR(14): seed with the mean of the first 14 true ranges that have a previous close; thereafter `atr = (previous_atr × 13 + true_range) / 14`.
- Require 1,000 consecutive completed warm-up candles before generating any signal.
- Persist the seed epoch and indicator checkpoint. Rebuild from that recorded history on recovery, not from a changing warm-up window that could alter the result.
- For candle `t`, channel high/low uses `t-20` through `t-1`. Strict inequalities are required; equality produces no signal.

A long requires `close[t] > EMA200[t]` and `close[t] > max(high[t-20:t])`. A short mirrors both comparisons. Do not use any partial candle, future row, centered rolling window or backward fill.

A signal expires 90 seconds after its candle closes. Entries occur only after closure. An existing position, pending intent, stale input or entry pause vetoes it. At most one attempt per strategy version, symbol and signal-close timestamp. After an exit, wait for a newly closed hourly candle; do not reverse or re-enter from an old signal.

### 6.3 Entry and exits

Use a marketable IOC limit entry, bounded by the entry slippage allowance. For a long, round the maximum permitted price down to the tick; for a short round the minimum permitted price up. Do not keep resting entry orders or chase unfilled quantities. A zero fill consumes the signal attempt.

Freeze signal ATR. From final average filled entry `P`, place a long stop at `P - 2×ATR` and short stop at `P + 2×ATR`. Round stops toward entry, then validate nonzero distance. Set the profit target at twice that actual rounded stop distance; round the target away from entry. Persist these fixed prices and the first-fill time.

Protect every observed partial fill immediately while IOC terminal status is being reconciled. A provisional close-all stop covers further fills of that same entry. Once the entry is terminal, derive final levels from its average fill. Install replacement protection before retiring provisional protection, and require that the final risk remains within the persisted entry budget. If protection, IOC terminal status or risk cannot be established within 5 seconds of the first observed fill, latch a fault and attempt to flatten. This deadline starts remediation; it cannot guarantee network execution.

After final establishment, never widen or trail the stop. No scale-in or profit compounding beyond the capped risk calculation.

Use exchange-hosted STOP_MARKET and TAKE_PROFIT_MARKET conditional orders, triggered by MARK_PRICE. Under One-way Mode, use the documented close-position semantics; do not combine incompatible close-position, quantity and reduce-only parameters. Normal/time/emergency exits use opposite-side reduce-only market orders for the confirmed remaining quantity. [S3]

Both exits can race. Neither may open a reverse position. Reconcile the winner, cancel the sibling and verify its terminal status before permitting another entry. Profit target is a gross price-distance target; net reward/risk will be lower after costs.

Close at 48 hours from first fill if still open. This exit and account-risk monitoring operate independently of the hourly signal schedule. Missing/unverifiable exchange protection triggers an immediate fault and attempted exit. Exits must not be rejected just because an opening-order minimum is no longer met; use the venue's closing-order rules.

## 7. Execution journal, state machine and recovery

### 7.1 Durable intent

A signal record is unique on `(mode, strategy_hash, symbol, close_timestamp)`. Before submitting an order, atomically reserve the single position slot and persist the intent, approved budget, ATR, sizing inputs and client identifier.

Derive exchange-compatible client identifiers of at most 36 characters from the run ID, signal identity, order role and generation. Store the full logical ID separately. Protective generations have their own identifiers; an unresolved request is never assigned a new ID as a retry.

Order states are `PREPARED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED`, with terminal alternatives `CANCELED`, `EXPIRED` and `REJECTED`, and nonterminal `UNKNOWN`. A terminal status does not undo already recorded fills.

Position phases are `FLAT`, `ENTRY_PENDING`, `PROTECTING`, `OPEN`, `EXIT_PENDING` and `RECONCILING`. Entry permission is a separate run state: `STARTING`, `PAUSED`, `RUNNING`, `DEGRADED`, `HALTING` or `HALTED`.

### 7.2 Unknown outcomes

A timeout or certain server errors can leave submission outcome unknown. Persist UNKNOWN, stop entries, and query order status by client ID, open orders, fills and positions. Resolve both ordinary and conditional order namespaces. Do not treat one immediate “not found” response as proof that execution never happened. Binance documents this ambiguity. [S1]

Poll reconciliation with bounded backoff; after 30 seconds unresolved, keep a persistent fault and continue slower read-only reconciliation while preserving protection. Never retry an unknown exposure-increasing request. Distinguish definitive rejection from ambiguity. Only a known terminal zero-fill rejection frees the slot without a position.

Disable automatic SDK retries for exchange mutations unless their precise behavior has been inspected and made compatible with this journal. Retry idempotent reads with bounded jitter/backoff, respecting rate-limit responses and headers. Prioritize risk-reducing operations and avoid request storms.

### 7.3 Startup and event ordering

Startup always disables entries. Acquire the process lock, validate migrations/configuration, load the persistent trial/halt, and reconcile exchange state before any Resume.

- Deduplicate fills by environment/account/symbol/trade ID and income by its venue transaction identity.
- Apply order cumulative quantities monotonically; late events cannot roll back a terminal state or double-book fees.
- Reconcile after stream reconnect; events alone are insufficient evidence of current balance.
- Preserve the earliest first-fill timestamp so restarts do not extend the 48-hour holding period.
- Reconcile all known protection generations and remove orphaned bot orders only after ownership and position state are established.
- An exchange position with no matching journal, an unexplained quantity difference, or external account activity requires operator review. Do not liquidate unidentified assets automatically.
- If the database is unavailable, corrupt or not durably writable, block entries. With known exposure, preserve exchange stops and attempt safe reduction from the last verified ownership snapshot, recording an emergency local log.
- Manual pause blocks entries while exits, protection and risk checks continue.

## 8. Data, backtesting and paper semantics

### 8.1 Data provenance

Use Binance Futures contract candles, corresponding mark-price candles and actual historical funding events. Spot data is not a substitute. Store compressed daily CSV files plus SHA-256 manifests, source, symbol, interval, UTC boundaries, retrieval time and filter snapshots. Stream files during replay; do not load a multi-year minute dataset into the VPS worker.

Reject duplicate/conflicting bars, impossible OHLC, nonfinite prices, nonpositive prices, negative volume and unexplained gaps. Records can be repaired by a new verified download; record the new dataset hash. Missing funding is unknown, not zero.

Research initially uses 24 complete months where data is available, plus warm-up history. Split chronologically 60% development, 20% validation and 20% final untouched evaluation. Fix the split before reporting. If sufficient complete history is unavailable, label the run insufficient; do not invent values.

Freeze a dated current exchange-filter snapshot for the main “tradable at today's rules” simulation. Explicitly label that it does not reconstruct historical exchange minima. Report selection/survivorship limitations of the fixed present-day universe. Live execution always refreshes current metadata.

### 8.2 Conservative execution simulation

Use a one-minute execution replay with one-hour signals, rather than filling every order at its signal close. Entry is modeled at the next minute's open strictly after the signal close, within the 90-second signal lifetime. This deliberately models additional delay; record the assumption.

For historical bid/ask without archived quotes, construct a synthetic spread of 5 basis points around the executable trade-price reference. Default modeled adverse fill slippage is 2 basis points on entry and 5 basis points on exit; these are distinct from the larger 5/10 basis-point sizing reserves. Apply commissions separately. Walk available depth in paper mode, respecting the IOC limit; never assume more displayed liquidity than the captured book supports or clip an otherwise unfillable price to the limit. Model IOC rejection when price/quantity/filter constraints are not met.

Protective triggers use minute mark OHLC; execution uses contract-trade prices. In an adverse gap, a stop fills at the worse available modeled execution price, not automatically at the stop level. If stop and target are both reachable in one minute and sequence is unknown, assume the stop first. Trigger reachability from a mark candle is not proof of a favorable execution price.

Process funding using actual event timestamps, side, quantity and funding mark price. For an event exactly on an entry/exit boundary, charge funding on an existing position before an exit, and process new entries after the funding event. This conservative ordering is explicit.

Check equity-floor breaches using the adverse intraminute mark for an existing position, including exit allowances. Apply trial halts in simulation. If liquidation precedes a modeled protective exit, mark the run failed for live eligibility; do not silently omit the event or claim an accurate liquidation fee without source data.

All unknown paths are labeled as modeling assumptions. Backtests cannot certify actual latency, fills, uptime or future profitability. Reports include a stress case with doubled spread, slippage and fee assumptions; retain actual historical funding.

### 8.3 Local and VPS paper runs

The paper broker uses production public quotes, mark prices and funding events with virtual balances. It passes through the same strategy, risk and journal interfaces. It cannot instantiate a private live client.

A paper run interrupted by shutdown, stale required data, clock discontinuity or a stream gap is marked INTERRUPTED and permanently excluded from continuous-forward-test evidence. Retain observed fills and last valuations; mark open virtual positions unresolved across the gap. Optional later offline replay belongs to a separate research run, never the forward ledger.

Restart local paper mode paused with a clear interruption message. A CLI “new paper session” creates a fresh run and virtual baseline; it does not fabricate an exit or continue an unknowable position as if it had been watched. This paper-only reset never resets a live trial.

VPS qualifying paper must run continuously. Any unexplained gap restarts the qualifying observation window through a new paper run. Demo mode verifies exchange order handling and recovery; its virtual balance, liquidity and filters are not substitutes for the small-balance paper experiment.

## 9. Storage and audit

Use one mode-specific operational SQLite database, migrations tracked by schema version, and immutable run/configuration hashes.

| Table | Required contents and invariants |
|---|---|
| runs | Mode, strategy/config/data hashes, baseline B, PHP display rate, floor F, start/end and qualification status |
| run_state | Entry permission, halt reason, last heartbeat, last reconciliation; one current row per run |
| signals | Unique decision identity, indicators, eligibility checks, result/rejection reason |
| order_intents | Logical/client IDs, role, generation, side, quantity, approved risk, request hash and outcome |
| orders | Venue namespace and ID, cumulative fill, status and timestamps |
| fills | Unique venue trade identity, price, quantity, commission amount/asset and attribution |
| positions | Owned symbol, side, quantity, average entry, frozen ATR/stop/target, first fill and phase |
| income_events | Funding/commission/transfer identities and raw accounting evidence |
| equity_snapshots | Mark equity, estimated closing equity, floor, timestamp and source freshness |
| control_commands | Unique request ID, action, operator, state and completion evidence |
| audit_events | Append-only ordered transitions, redacted payloads and reason codes |
| indicator_checkpoints | Seed epoch, last processed close, indicator state and source hash |
| web_sessions | Hashed random session tokens, creation/expiry and revocation |

Unique constraints and transactions enforce idempotency, not just in-memory checks. Do not erase a halt or baseline through migration or restore. Use SQLite backup APIs, verify integrity and perform a restore rehearsal. Keep daily backups with seven daily copies; preserve report manifests and configuration hashes. Restrict file permissions. Restoring an old backup requires exchange reconciliation and an operator checkpoint, never immediate trading.

Record detailed snapshots at most once per minute plus fills, funding and state transitions; keep higher-frequency decisions in bounded memory. Retain raw structured logs for 14 days with size rotation. Keep the trade/audit ledger for the life of the project.

## 10. Dashboard and security

Serve the dashboard on `127.0.0.1:8000` locally. On the VPS, bind the published port to loopback and access it through an SSH tunnel. No public website or domain is required.

Use a single operator login with an Argon2 password hash from environment configuration. Store random sessions server-side; use HttpOnly/SameSite=Strict cookies, CSRF tokens, allowed-origin/host validation and a 30-minute idle expiry. A local HTTP loopback connection must use the documented cookie configuration; use Secure cookies if HTTPS is introduced. Rate-limit failed logins to five per 15-minute window per account/source without blocking emergency CLI access.

Show:

- Prominent PAPER, DEMO or LIVE badge and data freshness.
- Worker state, connection health, active halt and last successful reconciliation.
- Mark equity and estimated closing equity, in USDT and fixed-rate PHP equivalents.
- Starting baseline, PHP 100 equivalent floor and remaining allowance.
- Position, gross/net P&L, fixed protective levels, confirmation status and 48-hour deadline.
- Trade/funding/fee history, equity chart and gross versus after-hosting results.
- Recent signals and plain reasons for skipped entries.
- Gate results, paper interruptions, strategy/configuration hashes and reports.

Controls:

| Control | Behavior |
|---|---|
| Pause new entries | Persistent entry pause; continue protection, risk monitoring and exits |
| Resume entries | Same baseline; require healthy reconciliation; cannot clear a loss halt |
| Close positions and pause | Confirm deliberate action, persist pause, cancel entries, reduce owned exposure and verify results |
| Download report | Export redacted ledger/metrics; never credentials |
| Acknowledge fault | Record review; does not by itself resume or clear accounting problems |

Configuration is read-only in the operational dashboard. Edits occur through versioned local configuration while flat, followed by restart and validation. No manual buy/sell, leverage override, baseline reset or mode switch in the dashboard.

Use only local static assets. Render charts with local SVG/JavaScript. Escape all venue messages, symbols and log content before HTML display. No API secrets, signed request URLs, private keys or session values in browser responses, reports or logs. API keys have no withdrawal permission and should use the VPS IP allowlist. Do not ask an implementation agent to paste secrets into code or chat.

CLI emergency control writes the same durable command queue. A request is displayed as pending until confirmed executed; a queued command is not proof that exposure is closed.

## 11. Deployment and operating cost

Use a non-root container, durable state/data bind mounts, a persistent restart policy, graceful signal handling and no public dashboard port. Code/dependency upgrades and schema migrations require confirmed flat real positions and no unresolved orders; never migrate an active live position into new code silently. Run one replica. Host clock synchronization, a firewall and SSH keys are required. Do not run CPU-intensive backtests on the live VPS.

Local graceful shutdown pauses entries and marks an active paper session interrupted. For LIVE, the normal stop procedure is “close positions and pause,” confirm flatness and cancel residual orders, then stop the service. A crash is recovered through exchange reconciliation.

Target a 1 vCPU/1 GiB instance only after a 72-hour paper resource rehearsal shows peak application/container memory below 700 MiB, average CPU below 25%, risk-loop p99 lag below 1 second, and no OOM/restart/backlog. Otherwise test 2 GiB. Include OS overhead, logs, data and backup space in sizing. Resource targets are engineering release checks, not measured results.

At the 2026-10-04 pricing check, DigitalOcean lists US$6/month for 1 GiB/1 vCPU and US$12/month for 2 GiB/1 vCPU, before taxes and extras. Future deployment must recheck prices. [S8]

Reports distinguish trading P&L from total economic P&L. Allocate selected recurring hosting/backup costs proportionally to elapsed time, showing the conversion assumption separately. Do not describe the project as self-funding when trading gains fail to cover operating costs.

## 12. Acceptance and release gates

| Gate | Required evidence |
|---|---|
| G1 — Deterministic logic | Golden EMA/ATR/breakout tests; causal replay; risk/rounding/property tests; no oversized or duplicate position |
| G2 — Execution reliability | Fault tests for unknown submissions, partial fills, rejected protection, stream gaps, crossed exit requests, crash recovery and SQLite failures |
| G3 — Historical evidence | Complete timestamped inputs; frozen split/assumptions; at least 50 closed trades in each base/stress untouched evaluation; positive net trading P&L in base and stressed cost cases; no trial-loss halt/liquidation; peak-to-trough drawdown no greater than 10% |
| G4 — Demo contract tests | Correct environment, settings, entry, partial/zero-fill handling, exchange-hosted SL/TP, safe exit semantics and restart reconciliation confirmed on supported demo APIs |
| G5 — Continuous paper | At least 30 uninterrupted days and 20 closed trades; no unresolved ledger mismatch, duplicate position, protection/recovery defect or trial-loss halt; positive net trading P&L |
| G6 — Operational/economic review | Resource rehearsal, backup/restore, private dashboard/auth tests, account eligibility and minimum-size checks; paper results positive after modeled chosen recurring hosting costs |
| G7 — Live arming | Operator reviews the evidence and explicitly arms a new, bounded live trial using Section 4 |

These are minimum research/engineering screens, not statistical proof of an edge. If counts are insufficient, extend observation; do not lower thresholds automatically. A bug fix, strategy change, material risk/fee change or dependency change affecting trading invalidates the corresponding evidence hashes. An untouched period loses that status if used to tune the strategy.

Protective API behavior that cannot be verified on demo must remain a live blocker; a successful syntax-only test order does not prove matching or protection behavior. No automated promotion between modes.

The PHP 1,000 balance may fail the feasibility or economic gates. Deliver a useful paper/research application and an explicit blocked-live report in that case.

## 13. Required deliverables from implementation

- Runnable Python project with locked dependencies and a reproducible Docker image.
- All four modes, with PAPER default and LIVE disabled.
- Dashboard, documented configuration and CLI commands.
- Migration scripts, example non-secret configuration, redacted sample reports and test fixtures.
- Automated tests and written demo/recovery/restore evidence.
- Data manifests, strategy/configuration hashes and qualification reports.
- Local setup and VPS runbooks, account preflight, incident and restore procedures.
- A final handoff stating which gates actually passed and which remain blocked. Do not fabricate a performance history or mark a 30-day trial complete after a shorter run.

## 14. Sources and verification notes

Primary sources checked on 2026-10-04. Endpoint paths, settings, prices and SDK versions must be revalidated when implementation begins. Strategy parameters, risk formulas, architecture and release thresholds are design decisions in this document, not exchange recommendations.

| ID | Primary source | Used for |
|---|---|---|
| S1 | [Binance USDⓈ-M General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info) | Environments, ambiguous responses and request limits |
| S2 | [Binance Futures market-data API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) and [Trading Parameters](https://www.binance.com/en/futures/trading-parameters) | Filters, candles, mark prices and funding metadata |
| S3 | [Binance Futures trade API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade) | Ordinary versus conditional orders and exit semantics |
| S4 | [Binance Python SDK repository](https://github.com/binance/binance-connector-python) | Maintained modular Python connector |
| S5 | [Binance Futures user-data API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/user-data-streams) | Stream establishment and renewal |
| S6 | [FastAPI templates](https://fastapi.tiangolo.com/advanced/templates/) | Server-rendered dashboard approach |
| S7 | [Binance Philippines](https://www.binance.com/en-PH) | Provider's description of the Philippine arrangement, not account verification |
| S8 | [DigitalOcean Droplet pricing](https://www.digitalocean.com/pricing/droplets) | Illustrative hosting configurations and current listed prices |
| S9 | [Freqtrade documentation](https://www.freqtrade.io/en/stable/) | Alternative considered; not selected as the implementation foundation |

