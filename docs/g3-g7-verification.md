# G3–G7 verification follow-up — 2026-10-05

The release remains blocked. This follow-up collects actual historical and account observations, fixes two compatibility defects found by those observations, and preserves the original 16 USDT research allocation, 14.40 USDT floor, 1% planned trade-risk cap and 2× leverage ceiling. No exchange orders, transfers or account-setting changes were performed; no live trial was armed.

| Gate | Bound result | Concrete observation |
| --- | --- | --- |
| G1 / G2 | PASS | Fresh functional suite and compatibility regressions pass; independent source review has no findings |
| G3 | FAIL | Both 24-month cases lose money and have zero untouched-holdout trades |
| G4 | NOT_YET_OBSERVED | Updated credentials work; account allocation/exposure/settings prevent real lifecycle testing |
| G5 | NOT_YET_OBSERVED | Short PAPER smoke is interrupted; no 30-day/20-trade qualifying run |
| G6 | NOT_YET_OBSERVED | Partial backup/access/resource evidence; account preconditions, 72-hour rehearsal and after-hosting forward economics remain unqualified |
| G7 | NOT_YET_OBSERVED | No LIVE acknowledgement or Resume; arming remains blocked |

The current [gate report](../reports/g3-g7-20261005/gates.json), [research summary](../reports/g3-g7-20261005/research/summary.json) and [partial observations](../reports/g3-g7-20261005/observations.json) remain in ignored private output directories. They replace the earlier tiny-sample assessment as the current local release evidence.

## Verified implementation

The locked environment sync, Ruff, mypy across 47 source files and `git diff --check` pass. The normal complete suite reports **172 passed, 1 opt-in demo skipped, 1 upstream Starlette/httpx warning in 106.09 seconds**. The separately opted-in demo case fails before entry because its wallet does not match the declared allocation; it is not a passing exchange lifecycle test.

Two fixes were developed from failing regression tests:

- The SDK transport now decodes decimal-valued JSON numbers from the original HTTP response text as `Decimal`, while retaining SDK signing, HTTP error handling, request ownership and disabled mutation retries. Actual leverage brackets previously became floats and were rejected by the strict financial boundary. Exact precision and serializable bracket exports are covered by the regression.
- Funding cadence compares reported settlements at second precision, while retaining the original millisecond timestamps and original rates/mark prices for accounting. The 24-month funding history contains small millisecond settlement delays. Historical validation, runtime funding context and replay risk reserves now use the same cadence. A replay regression first demonstrated an erroneous extra funding reserve for an eight-hour schedule with a 16 ms reporting delay. Missing settlements, changed schedules and duplicate settlements still fail regression tests.

Current package code hash: `19c96fb3796cff60be4eaea821d5452f05095663f6fb9739bd21de5d066027e2`.

Evidence logs live in ignored `artifacts/g3-g7-20261005/`, including `regression-red.log`, `regression-green.log`, `funding-regression-red.log`, `funding-regression-green.log`, `replay-funding-regression-red.log`, `replay-funding-regression-green.log`, and `final-suite-after-replay-fix.log`.

An independent read-only review reported no critical, important or minor findings and requested no changes. Its isolated mocked probes covered concurrent response capture, exact decoding, signing, mount delegation, HTTP errors and timeouts; a timeout issued one request. It approved merging the compatibility fixes and explicitly excluded release qualification. The review record is `artifacts/g3-g7-20261005/code-review.md`.

## G3 historical evidence

The fixed request covers **2024-10-01 through 2026-10-01 UTC**, with 1,000 preceding hourly warm-up candles. It retains the approved BTCUSDT/ETHUSDT/SOLUSDT universe and frozen chronological 60/20/20 split. No parameters were retuned against the holdout.

All **156 monthly trade/mark archives** were acquired from [Binance public data](https://github.com/binance/binance-public-data), totaling about 207.8 MiB, and verified against their published SHA-256 checksum files. Each symbol supplies 1,111,200 minute trade candles including warm-up. The mark archives omitted 2026-06-29 UTC; the official production mark-price kline endpoint returned the actual 1,440 missing candles for each symbol. The repairs, request source, retrieval timestamps and checksums are retained in `archive-quality.json` and the individual `*-rest-repair.json` files. No missing prices were synthesized.

Actual funding REST reads returned **2,315 settlements per symbol**. The observed raw timestamp differences vary by milliseconds, but every scheduled interval is eight hours and the full boundaries validate after the cadence fix. `funding-verified.json` records the fresh validation against the current code hash.

Current public contract filters and actual LIVE read-only first-tier maintenance brackets/taker fees are frozen as an explicit present-day approximation. Historical changes in filters, fees and maintenance tiers are not reconstructed. Fee floors and stress costs remain conservative as defined by the existing simulator. Archive provenance, actual REST repair provenance, maintenance export checksums, split and normalized files participate in the data binding.

The completed `frozen-v3` manifest binds 4,635 normalized files with data hash `aee0fe74accf0fae215ae06285297719d67bc4278a2d4cea56c7a41883a53106`. Both replay cases run the complete checksum, OHLC, continuity, boundary and funding validation before simulation. Partial normalization attempts remain separate and are not evaluated.

The untouched evaluation is **2026-05-08 through 2026-10-01 UTC**. The base case completed 168 trades, all in SOLUSDT, with **−1.543110603140437318 USDT net trading P&L** and **22.521394% full-period peak-to-trough drawdown**. Its final trade closed on 2026-04-16; it has **zero closed holdout trades and zero holdout P&L**. BTC/ETH minimum-size constraints and shrinking remaining risk capacity vetoed subsequent entries. It does not meet G3's positive net holdout or 50-trade requirements.

The base case also retains one `POST_FILL_BUDGET` failed assumption: the modeled fill exceeded its approved risk budget and was immediately closed. It records no trial-loss halt or liquidation. The completed input validation does not erase that execution observation. The full reports retain the fees, funding, reasons, equity and vetoes; they are not a statement of expected future returns.

The stress case doubles the existing spread, slippage and fee assumptions while retaining actual funding. It completed 82 SOLUSDT trades, with **−1.561818637575221463 USDT net trading P&L** and **15.243565% full-period drawdown**. It has no failed assumptions, trial-loss halt or liquidation, but also **zero closed holdout trades and zero holdout P&L**.

| Metric | Base | Stress |
| --- | --- | --- |
| Full-period closed trades | 168 | 82 |
| Full-period net trading P&L (USDT) | −1.543110603140437318 | −1.561818637575221463 |
| Full-period peak drawdown | 22.521394% | 15.243565% |
| Untouched-holdout closed trades | 0 | 0 |
| Untouched-holdout net trading P&L (USDT) | 0 | 0 |
| Untouched-holdout measured drawdown | 0% (no trading) | 0% (no trading) |
| Failed assumptions | `POST_FILL_BUDGET` | None |

The gate bundle uses the **holdout** trade counts, P&L and drawdown, rather than the whole-period trade count. Both cases fail the positive-net requirement and lack the required 50 holdout trades. The base bundle additionally withholds complete qualification because of its recorded failed assumption; the underlying input validation passed. The zero holdout drawdown is inactivity, not evidence of a profitable safe trial.

Base/stress reports retain the complete run, equity series, trade/funding CSVs and HTML summaries. Both use an explicit zero-hosting-cost historical scenario; no recurring provider cost or positive forward after-hosting result is established. Parameters, universe and allocation were not retuned to make these failed results pass.

## G4 demo contract status

The original demo credentials returned `-2015`. After the operator updated `.env`, authenticated private reads succeeded on the [official demo environment](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info).

The observed demo wallet exceeds the declared 16 USDT allocation and contains pre-existing exposure. No open ordinary/algo orders were observed; the configured symbols use one-way/single-asset/isolated settings and 2× leverage. BNB fee payment is enabled. It is not the flat dedicated rehearsal wallet. Exact account observations remain in ignored private artifacts.

The explicitly opted-in application contract test was run. Its final attempt reports **one failure in 15.76 seconds** at `validate_allocation`, before entry, stream/protection/restart assertions or any order mutation. The earlier attempt encountered a read timeout and is retained separately. Existing exposure was preserved. `demo-contract-final.log` and `demo-preflight.json` provide the concrete blocker.

Ordinary/conditional execution, safe closing, sibling cleanup, private reconnect, accepted-timeout behavior and partial/zero fills have not been established on this account. Public/private reads and SDK retry configuration do not satisfy those lifecycle requirements. A clean dedicated DEMO rehearsal and a deliberately approved allocation are still required; an existing large demo balance is not permission to increase the research allocation or bypass its guard.

## G5 forward PAPER status

An isolated native PAPER service was inspected through the authenticated in-app browser. Login and overview/diagnostics navigation passed, with zero browser console errors at inspection. It retained the 16 USDT baseline, 14.40 USDT floor, paused entries and flat owned position state.

This short run permanently recorded `REQUIRED_MARKET_SOURCE_STALE` and `INTERRUPTED`; it has zero closed trades and supplies no qualifying forward performance. It ran on the local PC while research/verification work contended for resources, so it is not a continuous-host qualification. The service was stopped after inspection and backup; the interrupted database is preserved. The ordinary `state/paper.sqlite3` was not replaced.

The 30 uninterrupted days, 20 closed trades, positive net P&L and absence of unresolved reliability defects remain unobserved. A new qualified run requires a fixed configuration, fresh maintenance metadata and a continuously operated host. Shutdown/restart cannot turn this interrupted run into qualifying history.

![Authenticated isolated PAPER smoke](../artifacts/g3-g7-20261005/paper-dashboard.png)

The screenshot records the isolated smoke configuration and absent release bundle; it does not show the aggregate gate report. The later funding fix changes the package hash, so the smoke is retained as partial functional evidence rather than a bound operational qualification.

## G6 operations and economics

Native backup and checksum-verified isolated restore passed. The restored state is `PAUSED_RECONCILIATION_REQUIRED`; its PAPER observation is interrupted and old web sessions are revoked. The copied database remains separate from normal state. The current normal suite also covers private access and backup/restore behavior.

The **30.5-second** resource sample reports **99.81 MiB peak RSS, 40.51% average CPU and 3.859-second p99 loop lag**, with its healthy-rehearsal flag false. It was taken under concurrent local research load and is not a 72-hour host measurement. It exceeds the CPU/lag targets in that short sample. No host size or recurring cost is justified by this observation.

LIVE private reads succeeded, but the observed wallet is unfunded. It is flat with no open ordinary/algo orders. Account settings are one-way/single-asset/isolated with auto margin off, but configured leverage exceeds 2× and BNB fees are enabled. They fail the bounded live preconditions. Exact balances/settings remain in ignored private artifacts. Public prices and actual ATR/bracket/fee reads show BTC/ETH minimum-size vetoes at the virtual 16 USDT allocation and a sizing-only SOL candidate. That candidate is not a trade signal, an account-settings approval or a funded live feasibility pass.

Trade-key permissions, account/product/region eligibility, exclusive funded allocation, a 72-hour uninterrupted resource rehearsal and positive forward results after chosen recurring hosting costs are still required. Read access alone does not certify them. No hosting was purchased or deployed and no modeled provider price was selected for this follow-up.

Docker's Linux daemon was not running at the current check. The earlier image/hash in `acceptance.md` belongs to the prior implementation and does not verify the revised package; current container build/runtime acceptance remains outstanding.

## G7 operator authorization

LIVE remains disabled. No new trial acknowledgement or authenticated LIVE Resume was recorded. G1–G6 must pass against the actual declared allocation, final configuration/code/strategy/data before the separate arming and Resume actions. The native PAPER smoke and successful credential reads do not authorize that promotion.

The documented CLI reproduces the gate report with `cbot gate evaluate --evidence reports/g3-g7-20261005/evidence.json --config config/paper.toml`. Explicit arming-refusal checks with the paper and existing live configurations returned exit 2 before allocation/acknowledgement prompts, private adapter creation or trial storage. The paper check reports G3 FAIL and G4–G6 NOT_YET_OBSERVED. The live check additionally rejects the different configuration binding. No database was created by either check. Logs and status records are in `arming-paper-refusal.log`, `arming-live-refusal.log` and `arming-refusal.json`.

## Retained evidence and next stage

- `data/g3-20261005/archive-provenance.json`: immutable archive URLs/checksums.
- `artifacts/g3-g7-20261005/archive-quality.json`: every archive's continuity check and actual REST gap repairs.
- `artifacts/g3-g7-20261005/live-preflight.json` and `demo-preflight.json`: sanitized read-only endpoint/settings/account observations; both record zero mutations.
- `artifacts/g3-g7-20261005/backups/` and `restored/`: checked backup and isolated restored state.
- `artifacts/g3-g7-20261005/resources-smoke.json`: short contended resource measurement.
- `reports/g3-g7-20261005/evidence.json`, `gates.json` and `gates-cli.json`: bound current assessment, independently reproduced by the CLI.
- `reports/g3-g7-20261005/observations.json`: partial facts and blockers retained without qualifying G4–G7.
- `reports/g3-g7-20261005/research/`: completed base/stress full runs, concise summary and JSON/CSV/HTML reports.

The current strategy/allocation does not satisfy historical release criteria. The next decision is a research review of that failure before promotion. A material research change requires new bound evidence and an evaluation protected from parameter selection; the reported holdout cannot be retuned into an untouched result. G4 still needs a clean bounded DEMO rehearsal, and G5/G6 need separately authorized continuous PAPER hosting with actual elapsed observation and economics. Passing functional tests, larger stakes or edited evidence flags cannot replace these requirements.

To reproduce the retained research and gate evaluation from this checkout:

```powershell
uv run cbot backtest --dataset data/g3-20261005/frozen-v3 --config config/backtest.toml --out reports/g3-g7-20261005/reproduced-base.json
uv run cbot backtest --dataset data/g3-20261005/frozen-v3 --config config/backtest.toml --stress --out reports/g3-g7-20261005/reproduced-stress.json
uv run cbot gate evaluate --evidence reports/g3-g7-20261005/evidence.json --config config/paper.toml
```

The ignored `run_history.py` helper also writes the base/stress report bundles. Real account observations are time-dependent and must be refreshed for qualification; the frozen public dataset remains bound to its recorded inputs and assumptions.
