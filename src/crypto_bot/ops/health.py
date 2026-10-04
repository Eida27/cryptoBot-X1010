import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from crypto_bot.storage.repository import Repository


@dataclass(frozen=True)
class ResourceReport:
    duration_seconds: float
    samples: int
    pid: int
    peak_memory_mib: float
    average_cpu_percent: float
    p99_loop_lag_seconds: float | None
    no_restart_oom_backlog: bool


def measure_resources(
    duration_seconds: int, pid: int | None = None, database: Path | None = None
) -> ResourceReport:
    if duration_seconds <= 0:
        raise ValueError("Positive measurement duration required")
    process = psutil.Process(pid or os.getpid())
    process.cpu_percent()
    start = time.monotonic()
    peak = 0.0
    cpu = []
    while time.monotonic() - start < duration_seconds:
        cpu.append(process.cpu_percent(interval=min(1, duration_seconds)))
        peak = max(peak, process.memory_info().rss / 1048576)
    lags: list[float] = []
    healthy = False
    if database:
        conn = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            trial = conn.execute(
                "SELECT run_id,qualification_status FROM runs WHERE active=1"
            ).fetchone()
            if trial:
                rows = conn.execute(
                    "SELECT payload FROM audit_events WHERE run_id=? AND kind='LOOP_TIMING' AND at_ms>=?",
                    (trial[0], int((time.time() - duration_seconds) * 1000)),
                )
                for row in rows:
                    lags.extend(json.loads(row[0])["lags"])
                healthy = (
                    trial[1] != "INTERRUPTED"
                    and len(lags) >= duration_seconds - 120
                    and max(lags, default=10) < 1
                )
        finally:
            conn.close()
    p99 = sorted(lags)[min(len(lags) - 1, math.ceil(len(lags) * 0.99) - 1)] if lags else None
    return ResourceReport(
        time.monotonic() - start, len(cpu), process.pid, peak, sum(cpu) / len(cpu), p99, healthy
    )


def assert_upgrade_safe(repo: Repository) -> None:
    trial = repo.current_trial()
    if (
        trial
        and trial.mode.value in {"DEMO", "LIVE"}
        and (
            repo.position()
            or repo.has_active_slot()
            or any(not repo.intent_state(i.logical_id).terminal for i in repo.intents())
        )
    ):
        raise ValueError("Upgrade requires confirmed flat account and no unresolved real orders")
