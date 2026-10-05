import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from crypto_bot.config import ConfigurationError, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="cbot", description="Private Futures research bot")
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(".env"),
        help="Local secret environment file; OS environment wins",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    auth = (
        commands.add_parser("auth")
        .add_subparsers(dest="action", required=True)
        .add_parser("set-password")
    )
    auth.add_argument("--out", type=Path, default=Path(".env"))
    config = commands.add_parser("config").add_subparsers(dest="action", required=True)
    validate = config.add_parser("validate")
    validate.add_argument("--config", type=Path, required=True)
    data = commands.add_parser("data").add_subparsers(dest="action", required=True)
    download = data.add_parser("download")
    download.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    download.add_argument("--months", type=int, default=24)
    download.add_argument("--warmup", type=int, default=1000)
    download.add_argument("--start")
    download.add_argument("--end")
    download.add_argument("--out", type=Path, required=True)
    download.add_argument(
        "--brackets", type=Path, help="Fresh read-only bracket/fee export to freeze"
    )
    backtest = commands.add_parser("backtest")
    backtest.add_argument("--dataset", type=Path, required=True)
    backtest.add_argument("--config", type=Path, default=Path("config/backtest.toml"))
    backtest.add_argument("--out", type=Path, required=True)
    backtest.add_argument("--stress", action="store_true")
    serve = commands.add_parser("serve")
    serve.add_argument("--config", type=Path, default=Path("config/paper.toml"))
    paper = commands.add_parser("paper").add_subparsers(dest="action", required=True)
    new_session = paper.add_parser("new-session")
    new_session.add_argument("--config", type=Path, default=Path("config/paper.toml"))
    control = commands.add_parser("control")
    control.add_argument("action", choices=["pause", "close-and-pause", "acknowledge-fault"])
    control.add_argument("--config", type=Path, default=Path("config/paper.toml"))
    report = commands.add_parser("report")
    report.add_argument("--run", required=True)
    report.add_argument("--config", type=Path, default=Path("config/paper.toml"))
    report.add_argument("--hosting-monthly-usd", default="0")
    report.add_argument("--usdt-per-usd", default="1")
    report.add_argument("--out", type=Path, required=True)
    gate = (
        commands.add_parser("gate")
        .add_subparsers(dest="action", required=True)
        .add_parser("evaluate")
    )
    gate.add_argument("--evidence", type=Path, required=True)
    gate.add_argument("--config", type=Path, default=Path("config/paper.toml"))
    trial = (
        commands.add_parser("trial").add_subparsers(dest="action", required=True).add_parser("arm")
    )
    trial.add_argument("--evidence", type=Path, required=True)
    trial.add_argument("--config", type=Path, required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--config", type=Path, default=Path("config/paper.toml"))
    backup.add_argument("--out", type=Path, required=True)
    restore = commands.add_parser("restore-check")
    restore.add_argument("--backup", type=Path, required=True, help="Backup JSON manifest")
    restore.add_argument(
        "--out", type=Path, required=True, help="New isolated mode-named SQLite path"
    )
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--config", type=Path, default=Path("config/paper.toml"))
    doctor.add_argument(
        "--export-brackets", type=Path, help="Read-only private metadata export for public research"
    )
    resources = commands.add_parser("resources")
    resources.add_argument("--config", type=Path, default=Path("config/paper.toml"))
    resources.add_argument("--pid", type=int, required=True, help="Running service process ID")
    resources.add_argument("--seconds", type=int, default=72 * 3600)
    resources.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "auth":
            import getpass

            from argon2 import PasswordHasher

            password = getpass.getpass("New dashboard password: ")
            if len(password) < 12 or password != getpass.getpass("Confirm dashboard password: "):
                raise ValueError("Use at least 12 characters and matching confirmation")
            content = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
            lines = [
                line for line in content.splitlines() if not line.startswith("CBOT_PASSWORD_HASH=")
            ]
            lines.append("CBOT_PASSWORD_HASH='" + PasswordHasher().hash(password) + "'")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            args.out.chmod(0o600)
            print("Dashboard password hash saved locally")
            return
        if args.command == "restore-check":
            from crypto_bot.ops.backup import BackupManifest, verify_restore

            print(
                json.dumps(
                    {
                        "path": str(
                            verify_restore(BackupManifest.read(args.backup), args.out).path
                        ),
                        "state": "PAUSED_RECONCILIATION_REQUIRED",
                    }
                )
            )
            return
        if args.command == "data":
            from crypto_bot.research.datasets import DatasetRequest, download_dataset

            end = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month = end.year * 12 + end.month - 1 - args.months
            start = end.replace(year=month // 12, month=month % 12 + 1)

            def utc_ms(value: str | None, default: datetime) -> int:
                date = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else default
                return (
                    int(date.replace(tzinfo=UTC).timestamp() * 1000)
                    if date.tzinfo is None
                    else int(date.timestamp() * 1000)
                )

            request = DatasetRequest(
                tuple(args.symbols.split(",")),
                utc_ms(args.start, start),
                utc_ms(args.end, end),
                args.warmup,
            )
            manifest = asyncio.run(
                download_dataset(request, args.out, bracket_metadata=args.brackets)
            )
            print(
                json.dumps(
                    {
                        "content_hash": manifest.content_hash,
                        "manifest": str(manifest.root / "manifest.json"),
                    }
                )
            )
            return
        from dotenv import dotenv_values

        environment = {k: v for k, v in dotenv_values(args.secrets).items() if v is not None}
        environment.update(os.environ)
        settings = load_settings(args.config, environment)
        if args.command == "backup":
            from crypto_bot.ops.backup import create_backup

            result_backup = create_backup(settings.database, args.out)
            print(json.dumps({"path": str(result_backup.path), "checksum": result_backup.checksum}))
            return
        if args.command == "resources":
            from crypto_bot.ops.health import measure_resources
            from crypto_bot.storage.repository import encode

            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                encode(measure_resources(args.seconds, args.pid, settings.database)),
                encoding="utf-8",
            )
            print(json.dumps({"measurement": str(args.out)}))
            return
        if args.command == "doctor":
            from crypto_bot.domain.enums import Mode
            from crypto_bot.exchange.binance_adapter import BinanceAdapter
            from crypto_bot.storage.database import Database
            from crypto_bot.storage.repository import Repository, encode
            from crypto_bot.web.views import build_dashboard_view

            async def diagnose() -> None:
                db = Database(settings.database)
                adapter = BinanceAdapter(settings, private=settings.mode in {Mode.DEMO, Mode.LIVE})
                try:
                    server = await adapter.read("check_server_time")
                    rules = [await adapter.fetch_rules(s) for s in settings.symbols]
                    if args.export_brackets:
                        if not adapter.private:
                            raise ConfigurationError(
                                "Bracket export requires an explicitly configured DEMO/LIVE read-only client"
                            )
                        brackets = {}
                        fees = {}
                        for symbol in settings.symbols:
                            brackets[symbol] = (await adapter.read("brackets", symbol=symbol))[0][
                                "brackets"
                            ][0]
                            fees[symbol] = (await adapter.read("commission", symbol=symbol))[
                                "takerCommissionRate"
                            ]
                        args.export_brackets.parent.mkdir(parents=True, exist_ok=True)
                        args.export_brackets.write_text(
                            encode(
                                {
                                    "environment": settings.mode.value,
                                    "observed_ms": adapter.clock.now_ms(),
                                    "brackets": brackets,
                                    "fees": fees,
                                }
                            ),
                            encoding="utf-8",
                        )
                    state = build_dashboard_view(Repository(db))
                    print(
                        encode(
                            {
                                "exchange_clock_delta_ms": int(server["serverTime"])
                                - adapter.clock.now_ms(),
                                "symbols": rules,
                                "state": state,
                                "mutations": 0,
                            }
                        )
                    )
                finally:
                    db.close()
                    if hasattr(adapter.transport, "close"):
                        adapter.transport.close()

            asyncio.run(diagnose())
            return
        if args.command == "gate":
            from crypto_bot.research.gates import EvidenceBundle, evaluate_gates
            from crypto_bot.storage.repository import encode

            print(encode(evaluate_gates(EvidenceBundle.read(args.evidence), settings)))
            return
        if args.command == "report":
            from decimal import Decimal

            from crypto_bot.domain.clock import SystemClock
            from crypto_bot.research.reports import (
                HostingCost,
                build_report,
                read_run,
                repository_run,
            )
            from crypto_bot.storage.database import Database
            from crypto_bot.storage.repository import Repository

            if Path(args.run).is_file():
                result = read_run(Path(args.run))
            else:
                db = Database(settings.database)
                try:
                    result = repository_run(Repository(db), args.run, SystemClock().now_ms())
                finally:
                    db.close()
            build_report(
                result, HostingCost(Decimal(args.hosting_monthly_usd), Decimal(args.usdt_per_usd))
            ).write(args.out)
            print(json.dumps({"report": str(args.out / "report.html")}))
            return
        if args.command == "trial":
            from decimal import Decimal

            from crypto_bot.domain.enums import PositionSide
            from crypto_bot.domain.models import Signal
            from crypto_bot.exchange.binance_adapter import BinanceAdapter
            from crypto_bot.market.service import MarketService
            from crypto_bot.research.gates import (
                EvidenceBundle,
                GateStatus,
                arm_trial,
                evaluate_gates,
            )

            bundle = EvidenceBundle.read(args.evidence)
            blocked = [
                g
                for g in evaluate_gates(bundle, settings).gates[:6]
                if g.status is not GateStatus.PASS
            ]
            if blocked:
                raise ValueError(
                    "Arming blocked: "
                    + "; ".join(f"{g.identity}: {g.status.value}" for g in blocked)
                )
            allocation = Decimal(input("Declared exclusive USDT allocation: "))
            acknowledgement = input("Type ARM NEW BOUNDED LIVE TRIAL: ")

            async def preflight() -> None:
                adapter = BinanceAdapter(settings, private=True)
                try:
                    snapshot = await adapter.fetch_snapshot()
                    verified_settings = []
                    for symbol in settings.symbols:
                        context = await MarketService(adapter, adapter).build_context(
                            Signal(
                                "preflight",
                                "",
                                symbol,
                                adapter.clock.now_ms(),
                                PositionSide.LONG,
                                Decimal("1"),
                                Decimal("1"),
                            )
                        )
                        verified_settings.append(context.settings)
                    for v in verified_settings:
                        from crypto_bot.research.gates import validate_arming_preflight

                        validate_arming_preflight(settings, allocation, snapshot, v)
                    trial = arm_trial(
                        bundle,
                        settings,
                        allocation,
                        acknowledgement,
                        snapshot=snapshot,
                        verified=verified_settings[0],
                        at_ms=adapter.clock.now_ms(),
                    )
                    print(json.dumps({"run_id": trial.run_id, "state": "PAUSED"}))
                finally:
                    if hasattr(adapter.transport, "close"):
                        adapter.transport.close()

            asyncio.run(preflight())
            return
        if args.command == "serve":
            import uvicorn

            from crypto_bot.app import build_service

            if os.environ.get("WEB_CONCURRENCY", "1") != "1":
                raise ConfigurationError("One service worker is required")
            uvicorn.run(
                build_service(settings),
                host=settings.bind_host,
                port=settings.port,
                workers=1,
                reload=False,
                access_log=False,
            )
            return
        if args.command in {"paper", "control"}:
            from crypto_bot.config import STRATEGY_HASH
            from crypto_bot.domain.clock import SystemClock
            from crypto_bot.domain.models import ControlCommand
            from crypto_bot.research.paper import new_paper_session
            from crypto_bot.storage.database import Database, ProcessLock
            from crypto_bot.storage.repository import Repository

            repo = Repository(Database(settings.database))
            try:
                if args.command == "paper":
                    with ProcessLock(settings.database):
                        fresh_trial = new_paper_session(
                            repo,
                            settings.initial_capital_usdt,
                            {"config": settings.config_hash, "strategy": STRATEGY_HASH},
                            SystemClock().now_ms(),
                        )
                    print(json.dumps({"run_id": fresh_trial.run_id, "state": "PAUSED"}))
                else:
                    identity = repo.enqueue_command(
                        ControlCommand(
                            uuid.uuid4().hex, args.action, "local-cli", SystemClock().now_ms()
                        )
                    )
                    print(json.dumps({"request_id": identity, "state": "PENDING"}))
            finally:
                repo.db.close()
            return
        if args.command == "backtest":
            from crypto_bot.research.datasets import load_manifest
            from crypto_bot.research.simulation import run_backtest
            from crypto_bot.storage.repository import encode

            result = run_backtest(load_manifest(args.dataset), settings, stress=args.stress)
            args.out.mkdir(parents=True, exist_ok=True)
            target = args.out / ("stress-run.json" if args.stress else "base-run.json")
            target.write_text(encode(result), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "run": str(target),
                        "closed_trades": len(result.trades),
                        "failed_assumptions": result.failed_assumptions,
                    }
                )
            )
            return
        print(json.dumps(settings.safe_dict(), indent=2))
    except (ConfigurationError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
