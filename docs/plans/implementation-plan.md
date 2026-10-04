# Crypto Futures Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved custom, low-cost Futures research and trading application with a private dashboard and a separately gated live trial.

**Architecture:** One Python service owns one asynchronous trading worker, one durable SQLite journal and one server-rendered dashboard. Shared strategy and risk functions drive both simulated and real execution through separate adapters. The dashboard sends durable commands; only the worker can mutate exchange orders.

**Tech Stack:** Python 3.12, uv, Binance's `binance-sdk-derivatives-trading-usds-futures`, FastAPI, Uvicorn, Jinja2, pydantic-settings, SQLite, Argon2, pytest, pytest-asyncio, Hypothesis, Ruff, mypy, Docker Compose and development-only Playwright.

**Spec:** [implementation-design-spec.md](../specs/implementation-design-spec.md), version 1.0, dated 2026-10-04. Read it completely before implementing.

**Execution scope:** These files are planning deliverables, not permission to place live orders or buy/deploy infrastructure. Implement in a new project location supplied for the execution task. If a repository exists there, inspect its instructions and preserve unrelated changes. Do not use the user's unrelated MedClinic repository. For economical implementation, native sequential execution is suitable; use delegation only when permitted by the user and applicable instructions.

## Global Constraints

- Python 3.12; manage and lock dependencies with uv.
- Default to PAPER with entries paused. Mode changes require service restart and a different mode-specific database.
- At most one open position across the entire bot.
- Planned risk per trade: at most 1% of current equity, capped at `0.01 × B`; include cost allowances.
- Set the loss floor `F = 0.90 × B`.
- Require an explicit `initial_capital_usdt`; never infer permission to trade a larger allocation from the account's balance.
- Maximum holding time: 48 hours.
- Isolated margin; leverage setting at most 2x; automatic margin replenishment disabled.
- No averaging down, increasing stakes after losses, or widening an established stop.
- Evaluate signals only after completed 1-hour candles; require 1,000 consecutive completed warm-up candles.
- Long/short breakouts use the preceding 20 candles, excluding the signal candle; trend filter is EMA(200); initial volatility distance is `2 × ATR(14)`.
- Use Decimal precision 34 for financial logic; decimal text in SQLite, never REAL.
- Never retry an unknown exposure-increasing request.
- A paper run with an observation gap is permanently excluded from continuous-forward-test evidence.
- The risk floor and halt persist across restart; ordinary Resume cannot clear a loss halt.
- Live release requires G1–G7 from the spec. Never fabricate missing performance history or shorten a 30-day trial.
- Production credentials, real funds, live orders and deployment are not needed for ordinary automated tests.

The spec controls any conflict. Record newly discovered API incompatibilities in `docs/compatibility.md` and keep live blocked until resolved; do not quietly change strategy or risk rules.

## Review Focus

1. **A larger wallet than declared capital:** arming must reject it instead of scaling the PHP 1,000 allocation. Pin this in Tasks 5 and 12.
2. **A partial fill followed by an ambiguous acknowledgement:** existing exposure needs protection even while the remainder is unresolved. Pin this in Tasks 8–10.
3. **Both exits execute or an old protective order survives a restart:** no reverse exposure or new trade until reconciliation is complete. Pin this in Tasks 9–10.
4. **Funding is booked twice or an external deposit masks losses:** equity must reconcile and the original floor must survive. Pin this in Tasks 2, 5 and 10.
5. **A dashboard action bypasses a halt or an overnight paper gap is counted as observed performance:** control and qualification checks must reject these paths. Pin this in Tasks 11–13.

---

## Repository structure

Keep the two planning files together at the repository root, or move both together into `docs/design/` and update their relative links. Source paths below are repository-relative.

| Paths | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock`, `.python-version` | Dependencies, Python version, CLI and tooling |
| `src/crypto_bot/config.py`, `cli.py`, `app.py` | Typed configuration, CLI and service composition |
| `src/crypto_bot/domain/enums.py`, `models.py`, `ports.py`, `clock.py` | Stable types, adapter contracts and injectable clock |
| `src/crypto_bot/storage/database.py`, `repository.py`, `migrations/001_initial.sql` | Durability, journal, commands and exact decimal persistence |
| `src/crypto_bot/market/service.py`, `validation.py`, `indicators.py` | Fresh market context and data checks |
| `src/crypto_bot/strategy/breakout.py` | Pure signal evaluation |
| `src/crypto_bot/risk/sizing.py`, `equity.py`, `eligibility.py`, `trial.py` | Sizing, equity, eligibility and persistent-halt decisions |
| `src/crypto_bot/exchange/binance_adapter.py`, `normalization.py`, `streams.py`, `errors.py` | SDK integration and exchange normalization |
| `src/crypto_bot/execution/coordinator.py`, `protection.py`, `reconciliation.py`, `worker.py` | Single-writer lifecycle and recovery |
| `src/crypto_bot/research/datasets.py`, `simulation.py`, `paper.py`, `reports.py`, `gates.py` | Data, simulated execution, metrics and evidence |
| `src/crypto_bot/web/auth.py`, `routes.py`, `views.py` | Sessions, commands and redacted view models |
| `src/crypto_bot/web/templates/`, `static/` | Login, overview, trades, diagnostics and local assets |
| `src/crypto_bot/ops/backup.py`, `health.py` | Backup, restore and operating measurements |
| `config/paper.toml`, `config/demo.toml`, `config/live.toml` | Non-secret mode profiles; live disabled |
| `Dockerfile`, `compose.yaml`, `.env.example`, `.gitignore` | Reproducible/private deployment |
| `tests/unit/`, `tests/integration/`, `tests/browser/`, `tests/demo/`, `tests/fixtures/` | Automated, browser and explicitly opted-in demo verification |
| `docs/compatibility.md`, `runbooks/`, `README.md` | Verified mappings and operating instructions |
| `var/`, `data/`, `reports/` | Ignored runtime artifacts; distinct paths for each mode/run |

Use focused files; do not put strategy, authentication and order execution in one module. Implement the tasks sequentially because interfaces and ownership matter.

## Task 1: Establish configuration and mode isolation

**Files:** Create `pyproject.toml`, `.python-version`, `src/crypto_bot/config.py`, `cli.py`, `domain/enums.py`, `config/*.toml`, `.env.example`, `.gitignore`, `tests/unit/test_config.py` and `docs/compatibility.md`. Produce `uv.lock`.

**Interfaces:**

- `Mode`: BACKTEST, PAPER, DEMO, LIVE.
- `load_settings(path: Path, environ: Mapping[str, str]) -> Settings`.
- `validate_mode(settings: Settings) -> None`; raise `ConfigurationError` on unsafe combinations.
- `cbot --help` and `cbot config validate --config PATH`.

- [ ] Read the spec's sources S1–S6 and verify compatible SDK/Python releases. Record the exact versions, environment URLs, conditional-order support, transport retry defaults and evidence date in `docs/compatibility.md`.
- [ ] Write the following configuration contracts before implementing validation:

~~~python
def test_paper_is_default_and_live_is_disabled(settings):
    assert settings.mode is Mode.PAPER
    assert settings.live_trading_enabled is False
    assert settings.entries_enabled is False

def test_live_rejects_local_host_and_missing_allocation(settings_factory):
    with pytest.raises(ConfigurationError):
        validate_mode(settings_factory(mode=Mode.LIVE, host_profile="local",
                                       initial_capital_usdt=None))
~~~

- [ ] Add rejection cases for leverage above 2, nonpositive allocation, NaN/infinite decimals, shared mode database paths, injected endpoint URLs and secrets in configuration rendering.
- [ ] Run `uv run pytest tests/unit/test_config.py -q`; verify failures reflect missing/incorrect behavior.
- [ ] Implement typed settings, enum/environment allowlists, secret redaction and the CLI entry point. Sample PAPER capital is 16.00 virtual USDT, explicitly labeled as an estimate for PHP 1,000. LIVE profile has no credentials and is disabled.
- [ ] Lock verified dependencies; run the tests and `uv run cbot config validate --config config/paper.toml`. Both must pass without private exchange access.
- [ ] Commit as `chore: establish isolated bot modes and locked runtime`.

## Task 2: Define domain contracts and durable storage

**Files:** Create `domain/models.py`, `ports.py`, `clock.py`, `storage/database.py`, `repository.py`, `migrations/001_initial.sql`, `tests/conftest.py`, `tests/fixtures/factories.py` and `tests/integration/test_storage.py`.

**Contract vocabulary:** `D` below means `Decimal`; timestamps are UTC milliseconds. Dataclasses are immutable unless they explicitly model repository transitions.

| Type | Required fields / meaning |
|---|---|
| `Candle` | symbol, open_ms, close_ms, open/high/low/close: D, volume/quote_volume: D, closed: bool |
| `PositionSide` / `OrderSide` | LONG/SHORT versus BUY/SELL; never interchange |
| `AccountSnapshot` | observed_ms, wallet_balance: D, unrealized_pnl: D, available_balance: D |
| `SymbolRules` | symbol, status, contract/quote/settle assets, tick_size, step_size, min_qty, max_qty, min_notional, bracket_limit: D, observed_ms |
| `BookLevel` | price: D, quantity: D |
| `MarketFrame` | symbol, observed_ms, bid/ask/mark: D, bids/asks: tuples of BookLevel, quote_volume_24h: D, freshness map |
| `FundingContext` | observed_ms, next_event_ms, interval_hours: D, current_rate: D, seven_day_max_abs_rate: D |
| `EntryContext` | account, market, rules, funding, verified settings, effective fee rates |
| `Signal` | stable identity, strategy_hash, symbol, close_ms, side, close_price: D, atr: D, indicator evidence |
| `Trial` | run_id, mode, baseline: D, floor: D, php_per_usdt: D, hashes, halt reason |
| `RiskInput` | trial, signal, context, policy and rounded initial price levels |
| `ApprovedSize` | quantity, notional, planned_loss, budget: D, frozen calculation evidence |
| `RejectedSize` | reason code, human-readable explanation and evidence |
| `OrderIntent` | logical/client IDs, role/generation, symbol, side, quantity/limit_price/trigger_price: D or None, request hash |
| `ExchangeSnapshot` | observed_ms, account, positions, ordinary orders, algo orders, fills and income |
| `SubmitAck / SubmitUnknown / SubmitRejected` | normalized transport outcomes; retain venue IDs and redacted evidence |
| `MarketEvent` | tagged BookEvent, MarkEvent, CandleEvent, FundingEvent or StreamGapEvent |
| `ExecutionEvent` | tagged OrderUpdate, FillEvent or IncomeEvent; stable source identity |
| `Clock` | `now_ms() -> int` and asynchronous `sleep(seconds: float) -> None` |

Define position phases, run states and order states exactly as in spec Section 7.

**Interfaces:**

- `Repository.reserve_entry(signal: Signal, size: ApprovedSize) -> OrderIntent | None`.
- `Repository.record_execution(event: ExecutionEvent) -> bool`; False means duplicate.
- `Repository.latch_halt(run_id: str, reason: str) -> None`.
- `Repository.enqueue_command(command: ControlCommand) -> str`.
- `Clock` implementations: `SystemClock` and deterministic `FakeClock`.

- [ ] Write tests for exact decimal round trips, duplicate income/fill identities, rollback on interrupted writes, persistent halt/baseline and two simultaneous reservation requests returning only one intent.
- [ ] Use this contract for accounting idempotency:

~~~python
def test_duplicate_funding_is_recorded_once(repo, funding_event):
    assert repo.record_execution(funding_event) is True
    assert repo.record_execution(funding_event) is False
    assert repo.count_income_events() == 1
~~~

- [ ] Run `uv run pytest tests/integration/test_storage.py -q` and confirm the intended failures.
- [ ] Implement all spec Section 9 tables, migrations and process locking. Use a transactional singleton active-slot constraint spanning pending entries and positions, not merely a count of filled positions.
- [ ] Add test factories with explicit Decimal-string defaults and a fake clock. Keep different mode databases independent.
- [ ] Re-run storage tests, then kill/reopen a temporary process and verify baseline, halt and a prepared intent survive.
- [ ] Commit as `feat: add durable trading journal and domain contracts`.

## Task 3: Implement normalized market and account access

**Files:** Create `exchange/binance_adapter.py`, `normalization.py`, `streams.py`, `errors.py`, `market/service.py`, `validation.py`, `tests/unit/test_normalization.py` and `tests/integration/test_exchange_reads.py`.

**Interfaces:**

- `ExchangePort.fetch_snapshot() -> ExchangeSnapshot`, async.
- `ExchangePort.fetch_rules(symbol: str) -> SymbolRules`, async.
- `ExchangePort.market_events(symbols: tuple[str, ...]) -> AsyncIterator[MarketEvent]`.
- `ExchangePort.execution_events() -> AsyncIterator[ExecutionEvent]` for authenticated order/fill/income updates; PAPER provides simulated events instead.
- `MarketService.build_context(signal: Signal) -> EntryContext`, async.
- `normalize_snapshot(payloads: Mapping[str, object]) -> ExchangeSnapshot`.
- Mutation methods are declared in `ExchangePort` now but are not implemented until Task 8.

- [ ] Capture redacted primary-schema fixtures for Futures filters, book/mark data, fees, settings, funding and balances. Do not use real credentials or account data in fixtures.
- [ ] Write tests for decimal preservation, exclusive candle-close timestamps, funding intervals other than eight hours, missing funding metadata, unknown schema fields, wrong settle asset and incomplete candles.
- [ ] Assert the following error behavior:

~~~python
def test_missing_funding_is_not_zero(raw_context):
    raw_context["funding"] = None
    with pytest.raises(MissingFundingData):
        normalize_entry_context(raw_context)
~~~

- [ ] Run `uv run pytest tests/unit/test_normalization.py tests/integration/test_exchange_reads.py -q` and confirm failures.
- [ ] Implement read methods and stream renewal/reconnect using the pinned SDK. Add `normalize_entry_context(payload: Mapping[str, object]) -> EntryContext`. Preserve raw redacted evidence for discrepancies.
- [ ] Implement freshness stamps from actual receipt/source times, not dashboard refresh time. Reads use bounded backoff; account events plus REST snapshots support reconciliation.
- [ ] Verify fake transports cannot route DEMO requests to production, and public PAPER code cannot obtain a private client.
- [ ] Commit as `feat: normalize Binance Futures data and account state`.

## Task 4: Implement causal indicators and the approved strategy

**Files:** Create `market/indicators.py`, `strategy/breakout.py`, `tests/unit/test_indicators.py` and `test_strategy.py`.

**Interfaces:**

- `update_indicators(state: IndicatorState, candle: Candle) -> IndicatorState`.
- `evaluate_signal(history: Sequence[Candle], state: IndicatorState, strategy_hash: str) -> Signal | None`.
- `IndicatorState` records EMA/ATR seeds, previous close, warm-up count, seed epoch and last processed close.

- [ ] Write literal-value tests: closes 1…200 seed EMA at 100.5; the next close 201 produces EMA 101.5. Hand-calculate ATR fixtures independently of implementation.
- [ ] Write strategy tests for both directions, equality, insufficient 999-candle warm-up, partial candles, exclusion of the signal candle from channel highs/lows, duplicate close times and data gaps.
- [ ] Pin causality with a future-data perturbation test:

~~~python
def test_future_rows_cannot_change_past_signals(replay, candles):
    before = replay(candles[:1200])
    extended = replay(candles[:1200] + unrelated_future_candles())
    assert extended[:len(before)] == before
~~~

- [ ] Run `uv run pytest tests/unit/test_indicators.py tests/unit/test_strategy.py -q`; inspect the intended failures.
- [ ] Implement the exact seed/recurrence definitions and strict breakout comparisons. Persist/recover the same seed epoch; never reseed a moving window.
- [ ] Verify a restarted replay produces exactly the same signals as uninterrupted replay.
- [ ] Commit as `feat: add deterministic hourly breakout strategy`.

## Task 5: Implement equity, risk sizing and affordability vetoes

**Files:** Create `risk/equity.py`, `sizing.py`, `eligibility.py`, `trial.py`, `tests/unit/test_risk.py`, `test_trial.py` and `test_eligibility.py`.

**Interfaces:**

- `mark_equity(account: AccountSnapshot) -> Decimal`.
- `closing_equity(account: AccountSnapshot, exit_allowance: Decimal) -> Decimal`.
- `size_entry(value: RiskInput) -> ApprovedSize | RejectedSize`.
- `check_trial(trial: Trial, account: AccountSnapshot, exit_allowance: Decimal) -> TrialDecision`.
- `check_eligibility(signal: Signal, context: EntryContext, now_ms: int) -> tuple[str, ...]`.
- `validate_allocation(declared: Decimal, wallet: Decimal) -> None`.
- `TrialDecision` has `halt: bool`, `reason: str | None` and the evaluated equity.

- [ ] Build a golden sizing fixture: B=20, E=20, available=20, entry=100, stop=98, step=0.01, minimum notional=5, 2x leverage; fee rates 0.0006 each, slippage reserves 0.0005/0.0010 and funding reserve 0.0007.
- [ ] Add these exact assertions:

~~~python
def test_rounding_never_increases_risk(risk_case):
    result = size_entry(risk_case())
    assert result.quantity == Decimal("0.08")
    assert result.notional == Decimal("8")
    assert result.planned_loss == Decimal("0.1872")
    assert result.budget == Decimal("0.20")

def test_minimum_notional_does_not_force_larger_order(risk_case):
    result = size_entry(risk_case(min_notional=Decimal("10")))
    assert result.reason == "BELOW_MIN_NOTIONAL"
~~~

- [ ] Add floor tests at E_guard=18.0001, 18 and 17.9999 for B=20; booked funding must not be subtracted twice. Assert wallet 200 versus declared 20 rejects arming. Test remaining budget near the floor, tick rounding, zero ATR, one-position limit, stale snapshots and cost changes before funding.
- [ ] Run `uv run pytest tests/unit/test_risk.py tests/unit/test_trial.py tests/unit/test_eligibility.py -q` and confirm failures.
- [ ] Implement spec Sections 5–6, including the E notional cap, collateral denominator, liquidity thresholds, fixed leverage and the 20% entry-to-liquidation cushion. Test both long and short inequalities against verified bracket fixtures and exchange-reported post-fill values.
- [ ] Add property tests proving every accepted quantity meets filters and budget, and every rejection leaves no order intent.
- [ ] Re-run tests and commit as `feat: enforce bounded trial risk and order affordability`.

## Task 6: Build auditable historical datasets

**Files:** Create `research/datasets.py`, `tests/unit/test_datasets.py` and `tests/fixtures/history/`. Extend CLI.

**Interfaces:**

- `download_dataset(request: DatasetRequest, destination: Path) -> DatasetManifest`, async.
- `validate_dataset(manifest: DatasetManifest) -> DataQualityReport`.
- `iter_events(manifest: DatasetManifest) -> Iterator[MarketEvent]`.
- `DatasetRequest` specifies symbols, UTC boundaries, intervals and warm-up; `DatasetManifest` includes files/checksums, retrieval/source metadata, filter snapshot and fixed split boundaries.
- `cbot data download --symbols BTCUSDT,ETHUSDT,SOLUSDT --months 24 --warmup 1000 --out PATH`.

- [ ] Write fixtures containing a missing minute, duplicate conflicting candle, invalid OHLC, negative volume, missing funding and an asset before its listing period.
- [ ] Assert validation fails with the affected symbol/time/reason and does not create a “complete” manifest. Assert repeated identical downloads produce the same normalized content hash.
- [ ] Run `uv run pytest tests/unit/test_datasets.py -q` to see the expected failures.
- [ ] Implement streamed compressed daily files, checksums, provenance and chronological 60/20/20 boundaries. Download Futures trade and mark data plus actual funding; do not use Spot data.
- [ ] Freeze and label current filter snapshots as current-tradability assumptions. Record gaps rather than filling them with invented prices or zero funding.
- [ ] Verify ingestion with small public samples only; full research downloads remain a separate CLI operation. Commit as `feat: add validated Futures research datasets`.

## Task 7: Implement conservative simulation

**Files:** Create `research/simulation.py`, `tests/unit/test_simulation.py` and `tests/integration/test_backtest.py`.

**Interfaces:**

- `SimulationBroker.apply(event: MarketEvent) -> tuple[ExecutionEvent, ...]`.
- `SimulationBroker.submit(intent: OrderIntent) -> SubmitAck | SubmitRejected`.
- `run_backtest(manifest: DatasetManifest, settings: Settings) -> RunResult`.
- `RunResult` contains run/hash identities, equity series, fills, funding, vetoes, interruptions and failed assumptions.
- `funding_cashflow(side: PositionSide, quantity: Decimal, mark: Decimal, rate: Decimal) -> Decimal`.
- `cbot backtest --dataset PATH --config PATH --out PATH`.

- [ ] Write a fixture where a signal closes at 10:00 UTC and entry occurs at 10:01, never at the signal close. Ensure the 90-second expiry still applies.
- [ ] Add gap-through-stop, simultaneous stop/target, filter rejection, liquidation failure and equity-floor-within-minute cases.
- [ ] Pin funding direction:

~~~python
def test_funding_direction():
    args = (Decimal("2"), Decimal("100"), Decimal("0.001"))
    assert funding_cashflow(PositionSide.LONG, *args) == Decimal("-0.2")
    assert funding_cashflow(PositionSide.SHORT, *args) == Decimal("0.2")
~~~

- [ ] Run `uv run pytest tests/unit/test_simulation.py tests/integration/test_backtest.py -q` and confirm failures.
- [ ] Implement spec Section 8: historical synthetic spread 5 bps; modeled fill slippage 2/5 bps entry/exit; larger sizing reserves remain 5/10 bps. Stress replay doubles spread, fill slippage and fee assumptions and sizes using the stressed costs.
- [ ] Reuse strategy/risk rules and journal accounting. Conservatively order ambiguous events; reject an IOC fill outside its limit instead of clipping it to a favorable price.
- [ ] Verify repeat runs produce identical ledgers and metrics for identical hashes. Commit as `feat: add conservative replay and funding simulation`.

## Task 8: Add durable entries and ambiguous-outcome handling

**Files:** Create `execution/coordinator.py`, extend `exchange/binance_adapter.py` and `domain/ports.py`; add `tests/integration/test_entry_execution.py`.

**Interfaces:**

- `ExchangePort.submit_entry(intent: OrderIntent) -> SubmitAck | SubmitUnknown | SubmitRejected`, async.
- `ExchangePort.find_order(intent: OrderIntent) -> OrderObservation`, async.
- `ExchangePort.cancel_order(intent: OrderIntent) -> SubmitAck | SubmitUnknown | SubmitRejected`, async.
- `ExecutionCoordinator.process_signal(signal: Signal, context: EntryContext) -> EntryOutcome`, async.
- `EntryOutcome` reports skipped, pending, partially filled, filled or unresolved, with intent identity.

- [ ] Build a FakeExchange with observable mutation calls, injectable accepted-but-timeout responses and eventually visible orders/fills.
- [ ] Test duplicate signal deliveries, a crash after the intent commit, definitive zero-fill rejection, IOC partial fill, out-of-order cumulative quantities and transport timeout after acceptance.
- [ ] Assert one submission despite repeated reconciliation:

~~~python
async def test_accepted_timeout_does_not_duplicate_entry(engine, exchange, signal, context):
    exchange.accept_then_timeout()
    await engine.process_signal(signal, context)
    await engine.process_signal(signal, context)
    assert exchange.entry_submission_count == 1
    assert engine.repo.has_unresolved_intent(signal.identity)
~~~

- [ ] Run `uv run pytest tests/integration/test_entry_execution.py -q` and confirm the failure before implementation.
- [ ] Implement deterministic client IDs, atomic slot reservation, bounded IOC prices, persisted sizing evidence and SDK mutation retry suppression.
- [ ] Treat UNKNOWN as occupied capacity. Query ordinary and conditional namespaces as appropriate; never free capacity on a single immediate not-found response.
- [ ] Verify all failure paths retain enough evidence for Task 10. Commit as `feat: journal entries and reconcile uncertain submissions`.

## Task 9: Implement protection, timed exits and shutdown

**Files:** Create `execution/protection.py`, extend coordinator/adapter; add `tests/integration/test_protection.py` and `test_shutdown.py`.

**Interfaces:**

- `ExchangePort.submit_protection(intent: OrderIntent) -> SubmitAck | SubmitUnknown | SubmitRejected`, async.
- `ExchangePort.reduce_position(symbol: str, side: PositionSide, quantity: Decimal, client_id: str) -> SubmitAck | SubmitUnknown | SubmitRejected`, async.
- `ProtectionManager.ensure_protection(intent_id: str) -> ProtectionResult`, async.
- `ExecutionCoordinator.request_exit(reason: ExitReason) -> ExitResult`, async.
- `ExitReason` includes TIME_LIMIT, TRIAL_LOSS, OPERATOR, PROTECTION_FAILURE, FUNDING_RISK and RECOVERY_FAULT.

- [ ] Test protection of the first partial fill, terminal-average entry anchoring, rounded fixed levels, provisional-to-final replacement and a 5-second protection-establishment deadline.
- [ ] Add cases for rejected/unconfirmed stop, take-profit already executed, simultaneous exit triggers, dust closing quantities and a rejected cancellation. Assert no reverse position is possible.
- [ ] Test a persisted first fill at T causes a time exit at T+48 hours even after restart.
- [ ] Run `uv run pytest tests/integration/test_protection.py tests/integration/test_shutdown.py -q` and confirm failures.
- [ ] Implement exchange-hosted SL and TP through current conditional APIs. Use verified close-position parameters and MARK_PRICE triggers; explicit reduce-only market exits use confirmed remaining quantity.
- [ ] For a loss/fault shutdown, commit the halt before cancel/exit calls. Retain the protective stop until confirmed flat. Cancel and confirm residual generations before freeing the slot.
- [ ] Preserve entry permission on ordinary strategy exits; operator close leaves PAUSED, while trial-loss and safety faults latch HALTED.
- [ ] Verify a failed exit is still displayed as exposure, then commit as `feat: enforce exchange protection and persistent shutdown`.

## Task 10: Reconcile restarts and external changes

**Files:** Create `execution/reconciliation.py` and `tests/integration/test_recovery.py`; extend repository normalization.

**Interfaces:**

- `Reconciler.recover(snapshot: ExchangeSnapshot) -> RecoveryResult`, async.
- `RecoveryResult` contains owned exposure, unresolved intents/orders, accounting mismatches and whether entry prerequisites are satisfied.
- `Repository.checkpoint_indicator(state: IndicatorState) -> None`.

- [ ] Add restart fixtures for an accepted order whose acknowledgement was lost, a filled TP with stale local OPEN state, a live stop with a missing local status update, and an unresolved sibling order.
- [ ] Test duplicate/late funding, an unexpected deposit, non-USDT commissions, unknown positions and a wallet larger than the declared budget.
- [ ] Pin the halt contract:

~~~python
async def test_restart_and_resume_do_not_reset_loss_floor(recovery_case):
    case = recovery_case(baseline="20", halted_for="TRIAL_LOSS")
    await case.restart()
    assert case.trial.floor == Decimal("18")
    assert case.resume_entries().accepted is False
~~~

- [ ] Run `uv run pytest tests/integration/test_recovery.py -q` and inspect expected failures.
- [ ] Implement reconciliation using orders, fills, income and positions. Preserve monotonic order state, fee attribution, first-fill time, the original baseline and indicator seed.
- [ ] Block on unknown ownership rather than adopting/liquidating unrelated exposure. Test SQLite unavailable/corrupt conditions: no entries; protection remains; emergency reduction is attempted only for verified owned exposure.
- [ ] Commit as `feat: recover durable state against exchange truth`.

## Task 11: Compose the worker and interruption-aware paper mode

**Files:** Create `execution/worker.py`, `research/paper.py`, `app.py`, `tests/integration/test_worker.py` and `test_paper_sessions.py`. Extend CLI.

**Interfaces:**

- `BotWorker.tick(now_ms: int) -> None`, async.
- `BotWorker.handle_command(command: ControlCommand) -> CommandResult`, async.
- `PaperBroker` implements the same execution port through simulation and captured public books.
- `mark_interrupted(run_id: str, reason: str, at_ms: int) -> None`.
- `cbot serve --config PATH`; `cbot paper new-session --config PATH`; `cbot control pause`; `cbot control close-and-pause`.

- [ ] Use FakeClock tests for 1-second risk evaluation, exposed 5-second REST refresh, flat 30-second refresh, stale data, missed funding update, hourly signal expiry and deterministic candidate ranking.
- [ ] Verify dashboard/CLI pause stops entries while the open position's risk and exit handling continue.
- [ ] Assert a paper gap permanently invalidates forward qualification:

~~~python
async def test_gap_is_not_forward_observation(paper_case):
    case = paper_case()
    await case.observe_gap(seconds=60)
    assert case.run.qualification_status == "INTERRUPTED"
    assert case.new_session().run_id != case.run.run_id
~~~

- [ ] Run `uv run pytest tests/integration/test_worker.py tests/integration/test_paper_sessions.py -q` and confirm failures.
- [ ] Implement the single worker, startup-paused lifecycle, serialized durable controls and mode-specific composition. Enforce the OS/database lock.
- [ ] Do not fabricate offline virtual fills. Preserve interrupted runs and unresolved virtual positions; a new local paper session is explicit and separate.
- [ ] Verify graceful stop and SIGKILL recovery with fake adapters. Commit as `feat: compose safe worker and interruption-aware paper sessions`.

## Task 12: Reports, evidence gates and live arming

**Files:** Create `research/reports.py`, `gates.py`, `tests/unit/test_metrics.py`, `test_gates.py` and `tests/integration/test_arming.py`. Extend CLI.

**Interfaces:**

- `build_report(run: RunResult, hosting: HostingCost) -> ReportBundle`.
- `evaluate_gates(evidence: EvidenceBundle, settings: Settings) -> GateReport`.
- `arm_trial(evidence: EvidenceBundle, settings: Settings, allocation: Decimal, acknowledgement: str) -> Trial`.
- `ReportBundle` includes machine-readable JSON, trades/funding CSV and an HTML report.
- `cbot report --run RUN_ID --hosting-monthly-usd AMOUNT --out PATH`.
- `cbot gate evaluate --evidence PATH --config PATH`.
- `cbot trial arm --config PATH --evidence PATH` prompts for the explicit live acknowledgement and declared allocation.

- [ ] Hand-calculate a small ledger's gross/net P&L, fees, funding, drawdown and pro-rated hosting to test metrics.
- [ ] Test insufficient trade counts (49 versus 50 historical; 19 versus 20 paper), 29 versus 30 uninterrupted days, insufficient counts in either base or stress holdout, mismatched hashes, missing actual funding, negative after-hosting P&L and paper interruptions.
- [ ] Assert flags alone cannot arm a trial, no new baseline is inferred from the wallet, and a loss-halted run cannot be resumed as a fresh trial by changing its name.
- [ ] Run `uv run pytest tests/unit/test_metrics.py tests/unit/test_gates.py tests/integration/test_arming.py -q` and confirm failures.
- [ ] Implement all G1–G7 checks with explicit PASS, FAIL or NOT_YET_OBSERVED outcomes. Missing evidence is never a pass.
- [ ] Bind evidence to capital/configuration/strategy/code/data hashes, record conversion assumptions and invalidate affected evidence after material changes.
- [ ] Require G1–G6 to pass before arming; record G7 through operator acknowledgement and the final Resume rather than creating a circular prerequisite. Require a flat, reconciled account for a new trial; archive earlier trials. Ordinary paper/report commands must never call arming.
- [ ] Commit as `feat: add transparent reports and explicit live qualification`.

## Task 13: Deliver the private dashboard and safe controls

**Files:** Create `web/auth.py`, `routes.py`, `views.py`, templates `login.html`, `overview.html`, `trades.html` and `diagnostics.html`; local `static/app.css` and `app.js`; add `tests/integration/test_web_security.py` and `tests/browser/test_dashboard.py`.

**Interfaces:**

- `create_app(settings: Settings, repository: Repository) -> FastAPI`.
- `build_dashboard_view(repository: Repository) -> DashboardView`; contains no SDK objects or secrets.
- `GET /`, `GET /trades`, `GET /diagnostics`, `GET /api/state` and authenticated report downloads.
- `POST /login`, `POST /logout`, `POST /commands/pause`, `POST /commands/resume`, `POST /commands/close-and-pause` and `POST /commands/acknowledge-fault`.

- [ ] Write auth tests for missing/expired sessions, absent/wrong CSRF tokens, cross-origin requests, disallowed Host, login throttling, escaped venue messages and secret redaction.
- [ ] Assert Pause and Close-and-pause enqueue different idempotent commands, require authentication, and cannot override a loss halt. Verify GET endpoints cannot mutate orders.
- [ ] Run `uv run pytest tests/integration/test_web_security.py -q` to confirm intended failures.
- [ ] Implement the spec's session and loopback/private-access policy. Add an interactive `cbot auth set-password` command using getpass; save only the resulting hash to the chosen local secret configuration without printing the password.
- [ ] Implement the fields and exact control labels in spec Section 10. Poll redacted state every 5 seconds; display pending versus confirmed actions separately.
- [ ] Run browser tests for PAPER/DEMO/LIVE badges, stale data, skip explanations, the PHP 100 equivalent allowance, expired sessions, paused-but-protected positions and responsive layouts.
- [ ] Verify at desktop and narrow widths without exposing a public site, then commit as `feat: add private dashboard and protected operator controls`.

## Task 14: Package the application and operational safeguards

**Files:** Create `Dockerfile`, `compose.yaml`, `ops/backup.py`, `ops/health.py`, `runbooks/local.md`, `vps.md`, `incidents.md`, `restore.md`, `README.md` and `tests/integration/test_backup_restore.py`.

**Interfaces:**

- `create_backup(database: Path, destination: Path) -> BackupManifest`.
- `verify_restore(manifest: BackupManifest, destination: Path) -> RestoreReport`.
- `measure_resources(duration_seconds: int) -> ResourceReport`.
- `cbot backup --out PATH`; `cbot restore-check --backup PATH --out PATH`; `cbot doctor --config PATH`.
- Private health endpoints report worker heartbeat, stream/account freshness, queue lag, protection status and halt reason, never secrets.

- [ ] Write a backup/restore test containing fills, baseline, a loss halt and a pending intent. Assert the restored database still requires reconciliation and cannot start entries.
- [ ] Run `uv run pytest tests/integration/test_backup_restore.py -q` and confirm failure.
- [ ] Implement SQLite backup/integrity checking, seven daily backups, 14-day rotated logs, bounded snapshots and disk-space checks.
- [ ] Build a non-root, digest-pinned image and loopback-only Compose port mapping with persistent volumes. Refuse multiple service replicas and production hot reload.
- [ ] Document Windows Docker/WSL2 and Linux local setup, SSH tunnel access, environment separation, updates, clock sync, orderly live shutdown and external-activity incidents. Require flatness and no unresolved real orders before upgrades/migrations.
- [ ] Include the measured-resource decision procedure: 72-hour paper rehearsal; below 700 MiB peak memory, below 25% average CPU, p99 risk-loop lag below 1 second, no OOM/restart/backlog before considering 1 GiB hosting.
- [ ] Verify cold start from an empty volume, restart and restore through the documented commands. Commit as `ops: package private deployment and recovery procedures`.

## Task 15: Perform end-to-end verification and produce an honest handoff

**Files:** Create `tests/integration/test_full_lifecycle.py`, `tests/demo/test_binance_contract.py`, `tests/demo/README.md` and `docs/acceptance.md`. Store generated reports outside git when they contain runtime/account data.

**Interfaces:** Consume the completed CLI, adapters, worker, dashboard and gate evaluator; add no alternate production order path.

- [ ] Write an end-to-end fake-exchange story: candle -> valid signal -> size -> IOC fill -> confirmed SL/TP -> funding -> exit -> reconciled ledger -> dashboard/report. Add a loss-floor branch and crash/restart branch.
- [ ] Run the integration story; verify exact ledger values, no duplicate/reverse position, persistent halt and accurate dashboard state.
- [ ] Run the normal verification commands:

~~~bash
uv sync --locked --group dev
uv run ruff check .
uv run mypy src/crypto_bot
uv run pytest tests/unit tests/integration -q
uv run pytest tests/browser -q
docker compose config
docker build -t crypto-bot:verification .
~~~

- [ ] Inspect failures and resolve their causes before proceeding. Ordinary commands must not submit demo or live exchange orders.
- [ ] Add an explicitly opted-in demo suite, skipped unless `RUN_BINANCE_DEMO_TESTS=1` and separate demo credentials are supplied locally. It must assert the official demo host before any mutation.
- [ ] On supported demo infrastructure, verify real order lifecycle behavior for ordinary/conditional orders, closing semantics, sibling cleanup, reconnects and SDK retries. Syntax-only test orders do not satisfy this task. If a behavior cannot be verified, mark the corresponding live gate blocked.
- [ ] Generate historical base/stress reports with frozen holdout boundaries; report infeasible trade sizes and insufficient data honestly.
- [ ] Hand over the local PAPER application once its functional checks pass. Start any continuous VPS paper observation only when that deployment is separately authorized; keep G5 as NOT_YET_OBSERVED until the required time and trade count exist.
- [ ] After the full observation period, evaluate economics, account eligibility and resource measurements. Present the actual evidence to the operator; do not automatically enable LIVE.
- [ ] Commit verified source/docs as `test: verify trading lifecycle and document release evidence`. State completed gates, blocked gates, measured costs/resources and outstanding operator actions in the handoff.

## Acceptance traceability

| Spec section | Owning tasks |
|---|---|
| 1–2: Brief, limits and scope | 1, 4, 5, 12, 15 |
| 3: Architecture/runtime | 1, 2, 3, 11, 13, 14 |
| 4: Modes/account ownership/arming | 1, 3, 10, 12, 15 |
| 5: Equity, sizing and shutdown | 2, 5, 9, 10, 11 |
| 6: Signals, selection and exits | 3, 4, 5, 8, 9, 11 |
| 7: Journal/recovery | 2, 8, 9, 10, 11 |
| 8: Data/backtest/paper semantics | 3, 6, 7, 11, 12 |
| 9: Storage/audit | 2, 10, 14 |
| 10: Dashboard/security | 1, 11, 13, 14 |
| 11: Hosting and economics | 12, 14, 15 |
| 12: G1–G7 release gates | 4–15, enforced by 12 |
| 13: Implementation deliverables | 1–15 |
| 14: Source verification | 1, 3, 8, 9, 15 |

## Completion criteria for the implementation agent

A completed implementation is not the same as a profitable or live-ready bot. Deliver code, reproducible setup, tests and the current gate report. If account access, minimum sizes, data availability, costs or the time required for paper observation prevent live release, stop at the appropriate research/paper stage with the blocker clearly recorded.

Do not solve a failed gate by increasing capital, leverage, risk, fees assumptions or the permitted loss without a new user decision and a versioned design change. Do not bypass restrictions or alter evidence. Preserve the user's PHP 1,000 allocation and PHP 100 total trial-loss threshold throughout.

