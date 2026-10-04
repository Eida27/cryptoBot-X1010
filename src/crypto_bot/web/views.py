import json
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

from crypto_bot.config import Settings
from crypto_bot.domain.clock import SystemClock
from crypto_bot.research.reports import HostingCost, build_report, repository_run
from crypto_bot.storage.repository import Repository, plain

DashboardView = dict[str, Any]


def build_dashboard_view(repository: Repository, settings: Settings | None = None) -> DashboardView:
    repo = repository
    trial = repo.current_trial()
    if trial is None:
        return {"mode": "PAPER", "state": "PAUSED", "reason": "No session", "position": None}
    state = repo.state()
    now = SystemClock().now_ms()
    latest = repo.db.connection.execute(
        "SELECT * FROM equity_snapshots WHERE run_id=? ORDER BY at_ms DESC LIMIT 1", (trial.run_id,)
    ).fetchone()
    mark = D(latest["mark_equity"]) if latest else trial.baseline
    closing = D(latest["closing_equity"]) if latest else trial.baseline
    run = repository_run(repo, trial.run_id, now)
    metrics = build_report(
        run,
        HostingCost(settings.hosting_monthly_usd, settings.usdt_per_usd)
        if settings
        else HostingCost(),
    ).metrics
    events = [
        plain(json.loads(r["payload"]))
        for r in repo.db.connection.execute(
            "SELECT payload FROM audit_events WHERE run_id=? ORDER BY id DESC LIMIT 30",
            (trial.run_id,),
        )
    ]
    commands = [
        dict(r)
        for r in repo.db.connection.execute(
            "SELECT request_id,action,at_ms,state,evidence FROM control_commands WHERE run_id=? ORDER BY at_ms DESC LIMIT 30",
            (trial.run_id,),
        )
    ]
    position = repo.position()
    return plain(
        {
            "mode": trial.mode.value,
            "run_id": trial.run_id,
            "state": state["state"],
            "reason": trial.halt_reason
            or (
                "INTERRUPTED"
                if trial.qualification_status == "INTERRUPTED"
                else "Entries paused"
                if state["state"] != "RUNNING"
                else "Observing"
            ),
            "qualification": trial.qualification_status,
            "baseline": trial.baseline,
            "floor": trial.floor,
            "mark_equity": mark,
            "closing_equity": closing,
            "allowance_usdt": max(D("0"), closing - trial.floor),
            "allowance_php": max(D("0"), closing - trial.floor) * trial.php_per_usdt,
            "php_per_usdt": trial.php_per_usdt,
            "maximum_trial_loss_php": D("100"),
            "reconciled": bool(state["reconciled"]),
            "heartbeat_age_seconds": (now - state["last_heartbeat"]) / 1000
            if state["last_heartbeat"]
            else None,
            "account_age_seconds": (now - state["last_reconciliation"]) / 1000
            if state["last_reconciliation"]
            else None,
            "position": position,
            "deadline_ms": position.first_fill_ms + 48 * 3600000 if position else None,
            "deadline_utc": datetime.fromtimestamp(
                (position.first_fill_ms + 48 * 3600000) / 1000, UTC
            ).isoformat()
            if position
            else None,
            "mark_equity_php": mark * trial.php_per_usdt,
            "closing_equity_php": closing * trial.php_per_usdt,
            "signals": [
                dict(r)
                for r in repo.db.connection.execute(
                    "SELECT symbol,close_ms,result FROM signals WHERE run_id=? ORDER BY close_ms DESC LIMIT 20",
                    (trial.run_id,),
                )
            ],
            "funding_history": repo.income_events(),
            "fee_history": repo.fills(),
            "metrics": metrics,
            "trades": run.trades,
            "equity": run.equity[-1440:],
            "events": events,
            "commands": commands,
            "hashes": trial.hashes,
            "gates": [{"identity": f"G{i}", "status": "NOT_YET_OBSERVED"} for i in range(1, 8)],
        }
    )
