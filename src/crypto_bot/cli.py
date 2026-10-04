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
    commands = parser.add_subparsers(dest="command", required=True)
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
    args = parser.parse_args()
    try:
        if args.command == "data":
            from crypto_bot.research.datasets import DatasetRequest, download_dataset
            end = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month = end.year * 12 + end.month - 1 - args.months
            start = end.replace(year=month // 12, month=month % 12 + 1)
            def utc_ms(value: str | None, default: datetime) -> int:
                date = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else default
                return int(date.replace(tzinfo=UTC).timestamp() * 1000) if date.tzinfo is None else int(date.timestamp() * 1000)
            request = DatasetRequest(tuple(args.symbols.split(",")), utc_ms(args.start, start),
                                     utc_ms(args.end, end), args.warmup)
            manifest = asyncio.run(download_dataset(request, args.out))
            print(json.dumps({"content_hash": manifest.content_hash, "manifest": str(manifest.root / "manifest.json")}))
            return
        settings = load_settings(args.config, os.environ)
        if args.command == "serve":
            import uvicorn
            from crypto_bot.app import build_service
            if os.environ.get("WEB_CONCURRENCY", "1") != "1":
                raise ConfigurationError("One service worker is required")
            uvicorn.run(build_service(settings), host=settings.bind_host, port=settings.port,
                        workers=1, reload=False, access_log=False)
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
                        trial = new_paper_session(repo, settings.initial_capital_usdt,
                            {"config": settings.config_hash, "strategy": STRATEGY_HASH}, SystemClock().now_ms())
                    print(json.dumps({"run_id": trial.run_id, "state": "PAUSED"}))
                else:
                    identity = repo.enqueue_command(ControlCommand(uuid.uuid4().hex, args.action,
                                                                   "local-cli", SystemClock().now_ms()))
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
            print(json.dumps({"run": str(target), "closed_trades": len(result.trades),
                              "failed_assumptions": result.failed_assumptions}))
            return
        print(json.dumps(settings.safe_dict(), indent=2))
    except (ConfigurationError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
