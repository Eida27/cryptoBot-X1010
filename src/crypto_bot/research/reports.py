import csv
import html
import io
import json
from dataclasses import dataclass
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from crypto_bot.research.simulation import RunResult
from crypto_bot.storage.repository import Repository, encode, plain


@dataclass(frozen=True)
class HostingCost:
    monthly_usd: D = D("0")
    usdt_per_usd: D = D("1")

    def __post_init__(self) -> None:
        if (
            not self.monthly_usd.is_finite()
            or self.monthly_usd < 0
            or not self.usdt_per_usd.is_finite()
            or self.usdt_per_usd <= 0
        ):
            raise ValueError("Invalid declared hosting/conversion assumption")


@dataclass(frozen=True)
class ReportBundle:
    metrics: dict[str, Any]
    json_text: str
    trades_csv: str
    funding_csv: str
    html_text: str

    def write(self, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for name, content in (
            ("report.json", self.json_text),
            ("trades.csv", self.trades_csv),
            ("funding.csv", self.funding_csv),
            ("report.html", self.html_text),
        ):
            (destination / name).write_text(content, encoding="utf-8")


def csv_text(rows: list[dict[str, Any]], fields: list[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # Also prevent spreadsheet formula execution in textual venue fields.
        writer.writerow(
            {
                k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "@")) else v
                for k, v in plain(row).items()
            }
        )
    return output.getvalue()


def max_drawdown(equity: tuple[tuple[int, D], ...], baseline: D) -> D:
    peak, drawdown = baseline, D("0")
    for _, value in equity:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, (peak - value) / peak)
    return drawdown


def build_report(run: RunResult, hosting: HostingCost) -> ReportBundle:
    realized = [i.amount for i in run.funding if i.income_type == "REALIZED_PNL"]
    gross = sum(realized, D("0")) if realized else sum((t.gross_pnl for t in run.trades), D("0"))
    fees = (
        sum((f.commission for f in run.fills), D("0"))
        if run.fills
        else sum((t.fees for t in run.trades), D("0"))
    )
    funding = (
        sum((f.amount for f in run.funding if f.income_type == "FUNDING_FEE"), D("0"))
        if run.funding
        else sum((t.funding for t in run.trades), D("0"))
    )
    net = gross - fees + funding
    host = (
        hosting.monthly_usd
        * hosting.usdt_per_usd
        * D(max(0, run.end_ms - run.start_ms))
        / D(30 * 86400000)
    )
    wins = sum(t.net_pnl > 0 for t in run.trades)
    profits = sum((max(D("0"), t.net_pnl) for t in run.trades), D("0"))
    losses = sum((max(D("0"), -t.net_pnl) for t in run.trades), D("0"))
    metrics = {
        "gross_pnl": gross,
        "fees": fees,
        "funding": funding,
        "net_trading_pnl": net,
        "hosting_usdt": host,
        "after_hosting_pnl": net - host,
        "monthly_hosting_usd_assumption": hosting.monthly_usd,
        "usdt_per_usd_assumption": hosting.usdt_per_usd,
        "php_per_usdt_fixed_assumption": D("1000") / run.baseline,
        "trial_loss_allowance_php": D("100"),
        "closed_trades": len(run.trades),
        "win_rate": D(wins) / len(run.trades) if run.trades else None,
        "profit_factor": profits / losses if losses else None,
        "max_drawdown": max_drawdown(run.equity, run.baseline),
        "mean_holding_hours": sum(
            (D(t.exit_ms - t.position.first_fill_ms) / 3600000 for t in run.trades), D("0")
        )
        / len(run.trades)
        if run.trades
        else None,
    }
    if run.split_boundaries:
        start, end = run.split_boundaries[2:]
        holdout = [t for t in run.trades if t.position.first_fill_ms >= start and t.exit_ms <= end]
        curve = tuple((at, v) for at, v in run.equity if start <= at <= end)
        starting = next((v for at, v in reversed(run.equity) if at <= start), run.baseline)
        metrics["untouched_evaluation"] = {
            "start_ms": start,
            "end_ms": end,
            "closed_trades": len(holdout),
            "net_trading_pnl": sum((t.net_pnl for t in holdout), D("0")),
            "max_drawdown": max_drawdown(curve, starting),
        }
    trades = [
        {
            "symbol": t.position.symbol,
            "side": t.position.side.value,
            "entry_ms": t.position.first_fill_ms,
            "exit_ms": t.exit_ms,
            "quantity": t.position.quantity,
            "entry": t.position.average_entry,
            "exit": t.exit_price,
            "gross_pnl": t.gross_pnl,
            "fees": t.fees,
            "funding": t.funding,
            "net_pnl": t.net_pnl,
            "reason": t.reason,
        }
        for t in run.trades
    ]
    payload = {"run": run, "metrics": metrics}
    rows = "".join(
        f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
        for k, v in metrics.items()
    )
    report_html = f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Research report</title><body><h1>{html.escape(run.mode.value)} research report</h1><p>Run {html.escape(run.run_id)}. Fixed conversion is an accounting assumption.</p><table>{rows}</table><h2>Evidence</h2><pre>{html.escape(encode(run.hashes))}</pre><h2>Limitations</h2><pre>{html.escape(encode([run.failed_assumptions, run.interruptions, run.vetoes]))}</pre></body></html>'
    return ReportBundle(
        metrics,
        encode(payload),
        csv_text(
            trades,
            [
                "symbol",
                "side",
                "entry_ms",
                "exit_ms",
                "quantity",
                "entry",
                "exit",
                "gross_pnl",
                "fees",
                "funding",
                "net_pnl",
                "reason",
            ],
        ),
        csv_text(
            [plain(f) for f in run.funding],
            ["symbol", "transaction_id", "income_type", "amount", "asset", "at_ms"],
        ),
        report_html,
    )


def repository_run(repo: Repository, run_id: str, end_ms: int) -> RunResult:
    from crypto_bot.domain.enums import Mode, PositionSide
    from crypto_bot.domain.models import Position
    from crypto_bot.research.simulation import ClosedTrade

    row = repo.db.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown run ID")
    fills = repo.fills(run_id)
    income = repo.income_events(run_id)
    equity = tuple(
        (r["at_ms"], D(r["mark_equity"]))
        for r in repo.db.connection.execute(
            "SELECT * FROM equity_snapshots WHERE run_id=? ORDER BY at_ms", (run_id,)
        )
    )
    trades: list[ClosedTrade] = []
    for r in repo.db.connection.execute(
        "SELECT payload FROM audit_events WHERE run_id=? AND kind='CLOSED_TRADE' ORDER BY at_ms",
        (run_id,),
    ):
        value = json.loads(r[0])
        p = value.pop("position")
        p["side"] = PositionSide(p["side"])
        from crypto_bot.domain.enums import PositionPhase

        p["phase"] = PositionPhase(p["phase"])
        for key in ("quantity", "average_entry", "atr", "stop", "target", "liquidation_price"):
            p[key] = D(p[key]) if p[key] is not None else None
        for key in ("exit_price", "gross_pnl", "fees", "funding", "net_pnl"):
            value[key] = D(value[key])
        trades.append(ClosedTrade(Position(**p), **value))
    interruptions = ("INTERRUPTED",) if row["qualification_status"] == "INTERRUPTED" else ()
    return RunResult(
        run_id,
        Mode(row["mode"]),
        D(row["baseline"]),
        row["start_ms"],
        row["end_ms"] or end_ms,
        json.loads(row["hashes"]),
        tuple(trades),
        equity,
        fills,
        income,
        (),
        interruptions,
        (),
    )


def read_run(path: Path) -> RunResult:
    from crypto_bot.domain.enums import Mode, OrderSide, PositionPhase, PositionSide
    from crypto_bot.domain.models import FillEvent, IncomeEvent, Position
    from crypto_bot.research.simulation import ClosedTrade

    data = json.loads(path.read_text(encoding="utf-8"))
    data = data.get("run", data)
    data["mode"], data["baseline"] = Mode(data["mode"]), D(data["baseline"])
    trades = []
    for value in data["trades"]:
        p = value.pop("position")
        p["side"], p["phase"] = PositionSide(p["side"]), PositionPhase(p["phase"])
        for key in ("quantity", "average_entry", "atr", "stop", "target", "liquidation_price"):
            p[key] = D(p[key]) if p[key] is not None else None
        for key in ("exit_price", "gross_pnl", "fees", "funding", "net_pnl"):
            value[key] = D(value[key])
        trades.append(ClosedTrade(Position(**p), **value))
    data["trades"] = tuple(trades)
    for fill in data["fills"]:
        fill["side"] = OrderSide(fill["side"])
        for key in ("price", "quantity", "commission"):
            fill[key] = D(fill[key])
    for income in data["funding"]:
        income["amount"] = D(income["amount"])
    data["fills"] = tuple(FillEvent(**v) for v in data["fills"])
    data["funding"] = tuple(IncomeEvent(**v) for v in data["funding"])
    data["equity"] = tuple((at, D(v)) for at, v in data["equity"])
    return RunResult(**data)
