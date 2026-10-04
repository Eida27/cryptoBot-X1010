# Private Futures research bot

Python 3.12, a locked uv environment, a private FastAPI dashboard, and one durable worker. PAPER is the default and every startup pauses entries. The fixed virtual starting balance is 16 USDT, representing the design's PHP 1,000 estimate; the conversion is an explicit accounting assumption. The original baseline and 90% floor are immutable for each trial. Costs and funding reduce the available risk budget.

From this checkout, install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run:

```powershell
uv sync --locked --group dev
uv run cbot config validate --config config/paper.toml
uv run cbot auth set-password --out .env
uv run cbot serve --config config/paper.toml
```

Open [the local dashboard](http://127.0.0.1:8000). It requires the password you set locally. Credentials belong in the ignored `.env` file or the process environment. No secrets need to be pasted into chat. OS environment values take precedence; use `cbot --secrets PATH ...` for another local secret file. Loopback HTTP uses HttpOnly/SameSite=Strict cookies with Secure disabled; enable Secure cookies with HTTPS. See [local setup](runbooks/local.md) and [VPS operations](runbooks/vps.md).

Research workflow:

```powershell
uv run cbot data download --symbols BTCUSDT,ETHUSDT,SOLUSDT --months 24 --warmup 1000 --out data/research
uv run cbot backtest --dataset data/research --config config/backtest.toml --out reports/research
uv run cbot backtest --dataset data/research --config config/backtest.toml --out reports/research --stress
uv run cbot report --run reports/research/base-run.json --hosting-monthly-usd 6 --out reports/base
uv run cbot gate evaluate --evidence docs/evidence-template.json --config config/paper.toml
```

The downloader streams minute trade/mark candles and actual funding into checksummed daily files, freezes chronological 60/20/20 boundaries and includes 1,000 hours of warm-up. Reports distinguish net trading P&L from declared recurring hosting costs (30-day proration, explicit USD/USDT conversion). Full data downloads are substantial; run research separately from any trading service.

Public metadata cannot prove account-specific maintenance brackets. PAPER/BACKTEST block entries when verified brackets or funding coverage are unavailable. A local, expiring read-only bracket export can be supplied as documented below; there are no invented maintenance rates or automatic capital increases. Minimum notional/step sizes may make the small allocation infeasible. Skips are recorded and reported.

Live release requires all G1–G6 evidence bound to allocation/config/strategy/code/data hashes, a fresh flat exclusive account, correct account settings and an explicit `cbot trial arm` acknowledgement. The service then remains paused until authenticated Resume (G7). DEMO and LIVE use separate credentials, endpoints, and databases. No mode promotes automatically. See [current acceptance evidence](docs/acceptance.md); implementation completion does not establish profitability or live readiness.

Emergency controls write durable requests:

```powershell
uv run cbot control pause --config config/paper.toml
uv run cbot control close-and-pause --config config/paper.toml
uv run cbot backup --out backups
uv run cbot restore-check --backup backups/paper-YYYY-MM-DD.json --out artifacts/restore/paper.sqlite3
```

A PENDING command is not proof of flatness. Loss halts cannot be cleared by Resume, renaming a run, restart or restore. A paper observation gap permanently interrupts that run; review preserved virtual exposure, stop the service and explicitly use `cbot paper new-session` for another paused observation.

Run verification with `uv run ruff check .`, `uv run mypy src/crypto_bot`, `uv run pytest tests/unit tests/integration -q`, and `uv run pytest tests/browser -q` after `uv run playwright install chromium`. Normal tests never submit exchange orders. [Demo tests](tests/demo/README.md) require an explicit separate opt-in and are excluded from qualification until their real lifecycle has been observed.

Docker packaging uses a digest-pinned Python image and runs as UID/GID 10001. Compose publishes only loopback, persists state and enforces one service process. The source is prepared for Docker; the acceptance document records whether a local build was actually verified. [Restore](runbooks/restore.md) and [incident](runbooks/incidents.md) procedures cover operational limits.
