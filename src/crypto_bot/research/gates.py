import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal as D
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_bot.config import STRATEGY_HASH, Settings, validate_mode
from crypto_bot.domain.enums import Mode
from crypto_bot.domain.models import ExchangeSnapshot, Trial, VerifiedSettings
from crypto_bot.storage.database import Database, ProcessLock
from crypto_bot.storage.repository import Repository, digest, encode


def code_hash() -> str:
    from importlib.metadata import distribution

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    package = Path(__file__).resolve().parents[1]
    manifest_path = package / "_build_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Required packaged build manifest is absent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not manifest.get("lock_sha256"):
        raise ValueError("Invalid packaged build manifest")
    source_lock = package.parent.parent / "uv.lock"
    if (
        source_lock.is_file()
        and hashlib.sha256(source_lock.read_bytes()).hexdigest() != manifest["lock_sha256"]
    ):
        raise ValueError("Source lock differs from packaged build manifest; regenerate it")
    versions = {canonicalize_name(k): v for k, v in manifest["versions"].items()}
    pending = [Requirement(value) for value in manifest["roots"]]
    visited = set()
    while pending:
        requirement = pending.pop()
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        if name in visited:
            continue
        visited.add(name)
        installed = distribution(name)
        if (
            versions.get(name) != installed.version
            or installed.version not in requirement.specifier
        ):
            raise ValueError(f"Installed dependency differs from build manifest: {name}")
        pending.extend(Requirement(value) for value in installed.requires or ())
    if not visited:
        raise ValueError("Empty dependency build manifest")
    files = sorted(
        path
        for path in package.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".json", ".sql", ".html", ".css", ".js"}
        and "__pycache__" not in path.parts
    )
    if not any(path.suffix == ".py" for path in files):
        raise ValueError("Installed code is absent from build manifest scope")
    hasher = hashlib.sha256()
    for path in files:
        hasher.update(path.relative_to(package).as_posix().encode())
        hasher.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return hasher.hexdigest()


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_YET_OBSERVED = "NOT_YET_OBSERVED"


@dataclass(frozen=True)
class Gate:
    identity: str
    status: GateStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class GateReport:
    gates: tuple[Gate, ...]

    def by_id(self, identity: str) -> Gate:
        return next(g for g in self.gates if g.identity == identity)


@dataclass(frozen=True)
class HistoricalEvidence:
    closed_trades: int
    net_pnl: D
    max_drawdown: D
    actual_funding: bool
    complete: bool
    loss_halt: bool
    liquidation: bool
    months: int
    hashes: dict[str, str]


@dataclass(frozen=True)
class PaperEvidence:
    observed_ms: int
    closed_trades: int
    net_pnl: D
    after_hosting_pnl: D
    interruptions: int
    ledger_mismatch: bool
    loss_halt: bool
    hashes: dict[str, str]


@dataclass(frozen=True)
class EvidenceBundle:
    hashes: dict[str, str]
    allocation: D
    logic_verified: bool | None = None
    execution_verified: bool | None = None
    historical_base: HistoricalEvidence | None = None
    historical_stress: HistoricalEvidence | None = None
    demo: dict[str, Any] | None = None
    paper: PaperEvidence | None = None
    operations: dict[str, Any] | None = None
    acknowledgement: str | None = None
    resumed: bool = False
    artifacts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def read(cls, path: Path) -> "EvidenceBundle":
        value = json.loads(path.read_text(encoding="utf-8"))
        value["allocation"] = D(value["allocation"])
        for name in ("historical_base", "historical_stress", "paper"):
            if value.get(name) is not None:
                raw = value[name]
                for key in ("net_pnl", "max_drawdown", "after_hosting_pnl"):
                    if key in raw:
                        raw[key] = D(str(raw[key]))
                value[name] = PaperEvidence(**raw) if name == "paper" else HistoricalEvidence(**raw)
        return cls(**value)


def evaluate_gates(evidence: EvidenceBundle, settings: Settings) -> GateReport:
    expected = {"config": settings.config_hash, "strategy": STRATEGY_HASH, "code": code_hash()}
    mismatch = (
        evidence.allocation != settings.initial_capital_usdt
        or any(evidence.hashes.get(k) != v for k, v in expected.items())
        or not evidence.hashes.get("data")
    )
    gates: list[Gate] = []

    def add(identity: str, failure: list[str], missing: list[str]) -> None:
        if mismatch:
            failure.insert(0, "Capital/configuration/strategy/code/data binding is invalid")
        gates.append(
            Gate(
                identity,
                GateStatus.FAIL
                if failure
                else GateStatus.NOT_YET_OBSERVED
                if missing
                else GateStatus.PASS,
                tuple(failure or missing or ["Verified against bound evidence"]),
            )
        )

    for identity, verified in (
        ("G1", evidence.logic_verified),
        ("G2", evidence.execution_verified),
    ):
        add(
            identity,
            ["Verification failed"] if verified is False else [],
            ["Verification evidence absent"] if verified is None else [],
        )
    fail, missing = [], []
    for label, case in (("base", evidence.historical_base), ("stress", evidence.historical_stress)):
        if case is None:
            missing.append(f"{label}: historical holdout not observed")
            continue
        if (
            case.hashes != evidence.hashes
            or not case.actual_funding
            or not case.complete
            or case.loss_halt
            or case.liquidation
            or case.net_pnl <= 0
            or not D("0") <= case.max_drawdown <= D("0.10")
        ):
            fail.append(f"{label}: incomplete/unprofitable/unsafe historical evidence")
        if case.months < 24 or case.closed_trades < 50:
            missing.append(f"{label}: requires 24 months and at least 50 closed holdout trades")
    add("G3", fail, missing)
    demo = evidence.demo
    demo_keys = (
        "ordinary_lifecycle",
        "conditional_lifecycle",
        "close_semantics",
        "sibling_cleanup",
        "reconnect",
        "mutation_retry_suppression",
        "partial_zero_fill",
        "private_reconnect",
    )
    add(
        "G4",
        ["Demo lifecycle failed or hashes changed"]
        if demo
        and (demo.get("hashes") != evidence.hashes or any(demo.get(k) is False for k in demo_keys))
        else [],
        ["Real opted-in demo lifecycle evidence absent"]
        if not demo or not all(demo.get(k) is True for k in demo_keys)
        else [],
    )
    paper = evidence.paper
    add(
        "G5",
        ["Forward observation interrupted, unsafe, unprofitable or hashes changed"]
        if paper
        and (
            paper.hashes != evidence.hashes
            or paper.interruptions
            or paper.ledger_mismatch
            or paper.loss_halt
            or paper.net_pnl <= 0
        )
        else [],
        ["Requires 30 uninterrupted days and 20 closed paper trades"]
        if paper is None or paper.observed_ms < 30 * 86400000 or paper.closed_trades < 20
        else [],
    )
    ops = evidence.operations
    failures: list[str] = []
    absent: list[str] = []
    if paper and paper.after_hosting_pnl <= 0:
        failures.append("Trading is not profitable after declared hosting cost")
    if not ops:
        absent.append("Operational/resource/account eligibility evidence absent")
    else:
        if ops.get("hashes") != evidence.hashes:
            failures.append("Operational evidence hashes differ")
        for key in (
            "backup_restore",
            "private_access",
            "account_eligible",
            "capital_feasible",
            "clock_sync",
            "no_restart_oom_backlog",
        ):
            if ops.get(key) is False:
                failures.append(f"{key} failed")
            elif ops.get(key) is not True:
                absent.append(f"{key} not observed")
        if int(ops.get("duration_seconds", 0)) < 72 * 3600:
            absent.append("72-hour resource rehearsal absent")
        for key, maximum in (
            ("peak_memory_mib", D("700")),
            ("average_cpu_percent", D("25")),
            ("p99_loop_lag_seconds", D("1")),
        ):
            if key not in ops:
                absent.append(f"{key} not measured")
            elif (
                not D(str(ops[key])).is_finite()
                or D(str(ops[key])) < 0
                or D(str(ops[key])) >= maximum
            ):
                failures.append(f"{key} exceeds limit")
    if paper is None:
        absent.append("After-hosting paper economics absent")
    add("G6", failures, absent)
    add(
        "G7",
        [],
        []
        if evidence.acknowledgement == "ARM NEW BOUNDED LIVE TRIAL" and evidence.resumed
        else ["Explicit new-trial acknowledgement and authenticated Resume not observed"],
    )
    return GateReport(tuple(gates))


def validate_arming_preflight(
    settings: Settings,
    allocation: D,
    snapshot: ExchangeSnapshot,
    verified: VerifiedSettings | None = None,
) -> None:
    validate_mode(settings)
    if (
        settings.mode is not Mode.LIVE
        or not settings.live_trading_enabled
        or settings.host_profile != "vps"
    ):
        raise ValueError("LIVE requires explicit flag and continuously operated VPS")
    if (
        not allocation.is_finite()
        or allocation <= 0
        or allocation != settings.initial_capital_usdt
        or abs(snapshot.account.wallet_balance - allocation) > D("0.01")
    ):
        raise ValueError("Declared allocation must match the exclusive wallet within 0.01 USDT")
    if snapshot.positions or any(
        not o.state.terminal for o in (*snapshot.ordinary_orders, *snapshot.algo_orders)
    ):
        raise ValueError("New trial requires a flat account and no unresolved orders")
    if verified is None:
        raise ValueError("Verified account settings are required")
    if (
        not verified.one_way
        or not verified.single_asset
        or not verified.isolated
        or verified.auto_margin
        or verified.bnb_fees
        or not D("1") <= verified.leverage <= 2
    ):
        raise ValueError("Account settings do not satisfy bounded-trial requirements")


def arm_trial(
    evidence: EvidenceBundle,
    settings: Settings,
    allocation: D,
    acknowledgement: str,
    *,
    snapshot: ExchangeSnapshot | None = None,
    verified: VerifiedSettings | None = None,
    at_ms: int = 0,
) -> Trial:
    gates = evaluate_gates(evidence, settings)
    blocked = [g for g in gates.gates[:6] if g.status is not GateStatus.PASS]
    if blocked:
        raise ValueError(
            "Arming blocked: " + "; ".join(f"{g.identity}: {g.status.value}" for g in blocked)
        )
    if acknowledgement != "ARM NEW BOUNDED LIVE TRIAL":
        raise ValueError("Exact explicit acknowledgement required")
    if snapshot is None:
        raise ValueError("Fresh reconciled account preflight required")
    if at_ms <= 0 or not 0 <= at_ms - snapshot.account.observed_ms <= 15000:
        raise ValueError("Fresh account preflight required")
    validate_arming_preflight(settings, allocation, snapshot, verified)
    with ProcessLock(settings.database):
        db = Database(settings.database)
        try:
            repo = Repository(db)
            previous = repo.current_trial()
            if previous:
                if repo.position() or repo.has_active_slot():
                    raise ValueError("Prior trial must be reconciled flat with cleared capacity")
                db.connection.execute(
                    "UPDATE runs SET active=0,end_ms=? WHERE run_id=?", (at_ms, previous.run_id)
                )
            hashes = {**evidence.hashes, "evidence": digest(evidence), "armed": "true"}
            trial = repo.create_run(Mode.LIVE, allocation, hashes, at_ms)
            db.connection.execute(
                "INSERT INTO audit_events(run_id,at_ms,kind,payload) VALUES(?,?,'ARMED',?)",
                (trial.run_id, at_ms, encode({"acknowledgement": acknowledgement, "gates": gates})),
            )
            # Startup remains PAUSED; fresh reconciliation and authenticated Resume are separate.
            return trial
        finally:
            db.close()
