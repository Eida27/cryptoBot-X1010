from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

import pytest


def make_repo(path):
    from crypto_bot.domain.enums import Mode
    from crypto_bot.storage.database import Database
    from crypto_bot.storage.repository import Repository

    repo = Repository(Database(path))
    if repo.current_trial() is None:
        repo.create_run(Mode.PAPER, D("20"), {"config": "test"}, 1000)
    return repo


def test_exact_decimals_and_idempotent_funding(tmp_path):
    from crypto_bot.domain.models import IncomeEvent

    repo = make_repo(tmp_path / "paper.sqlite3")
    event = IncomeEvent(
        "PAPER",
        "virtual",
        "SOLUSDT",
        "t1",
        "FUNDING_FEE",
        D("-0.0000000000000000000000000000000001"),
        "USDT",
        2000,
    )
    assert repo.record_execution(event)
    assert not repo.record_execution(event)
    assert repo.count_income_events() == 1
    assert repo.income_events()[0].amount == event.amount
    assert (
        repo.db.connection.execute("SELECT typeof(amount) FROM income_events").fetchone()[0]
        == "text"
    )


def test_duplicate_fill_does_not_book_commission_twice(tmp_path):
    from crypto_bot.domain.models import FillEvent

    repo = make_repo(tmp_path / "paper.sqlite3")
    fill = FillEvent(
        "PAPER",
        "virtual",
        "SOLUSDT",
        "1",
        "order1",
        D("100.01"),
        D("0.08"),
        D("0.00480048"),
        "USDT",
        2000,
    )
    assert repo.record_execution(fill)
    assert not repo.record_execution(fill)
    assert len(repo.fills()) == 1


def test_interrupted_write_rolls_back(tmp_path):
    repo = make_repo(tmp_path / "paper.sqlite3")
    with pytest.raises(RuntimeError):
        with repo.db.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_events(run_id,at_ms,kind,payload) VALUES(?,1,'bad','{}')",
                (repo.current_trial().run_id,),
            )
            raise RuntimeError("interrupted")
    assert not repo.db.connection.execute("SELECT 1 FROM audit_events WHERE kind='bad'").fetchone()


def test_baseline_and_loss_halt_survive_reopen(tmp_path):
    path = tmp_path / "paper.sqlite3"
    repo = make_repo(path)
    trial = repo.current_trial()
    repo.latch_halt(trial.run_id, "TRIAL_LOSS")
    repo.db.close()
    restored = make_repo(path)
    assert restored.current_trial().baseline == D("20")
    assert restored.current_trial().floor == D("18")
    assert restored.current_trial().halt_reason == "TRIAL_LOSS"
    assert not restored.resume(trial.run_id)


def test_two_reservations_share_single_durable_slot(tmp_path):
    from crypto_bot.domain.enums import PositionSide
    from crypto_bot.domain.models import ApprovedSize, Signal

    path = tmp_path / "paper.sqlite3"
    repo = make_repo(path)
    size = ApprovedSize(D("0.08"), D("8"), D("0.1872"), D("0.2"), {"entry": "100"})
    signals = [
        Signal(str(i), "strategy", symbol, 3600000, PositionSide.LONG, D("100"), D("1"))
        for i, symbol in enumerate(["BTCUSDT", "SOLUSDT"])
    ]

    def reserve(signal):
        other = make_repo(path)
        try:
            return other.reserve_entry(signal, size)
        finally:
            other.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, signals))
    assert sum(value is not None for value in results) == 1
    repo.db.close()
    restored = make_repo(path)
    assert restored.has_active_slot()
    assert len(restored.intents()) == 1


def test_commands_are_durable_and_idempotent(tmp_path):
    from crypto_bot.domain.models import ControlCommand

    repo = make_repo(tmp_path / "paper.sqlite3")
    command = ControlCommand("request1", "pause", "operator", 1000)
    assert repo.enqueue_command(command) == repo.enqueue_command(command)
    assert len(repo.pending_commands()) == 1


def test_process_lock_rejects_second_owner(tmp_path):
    from crypto_bot.storage.database import ProcessLock

    with ProcessLock(tmp_path / "paper.sqlite3"):
        with pytest.raises(RuntimeError, match="already"):
            with ProcessLock(tmp_path / "paper.sqlite3"):
                pass


def test_committed_intent_and_halt_survive_killed_process(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "paper.sqlite3"
    script = """
import sys, time
from pathlib import Path
from decimal import Decimal as D
from crypto_bot.storage.database import Database
from crypto_bot.storage.repository import Repository
from crypto_bot.domain.enums import Mode
from crypto_bot.domain.models import ApprovedSize
from tests.fixtures.factories import signal
repo = Repository(Database(Path(sys.argv[1])))
trial = repo.create_run(Mode.PAPER, D('20'), {}, 0)
repo.reserve_entry(signal(), ApprovedSize(D('0.08'), D('8'), D('0.1872'), D('0.2')))
repo.latch_halt(trial.run_id, 'TRIAL_LOSS')
print('committed', flush=True)
time.sleep(60)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(path)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout.readline().strip() == "committed"
    finally:
        process.kill()
        process.wait(timeout=10)
    restored = make_repo(path)
    assert restored.current_trial().floor == D("18.00")
    assert restored.current_trial().halt_reason == "TRIAL_LOSS"
    assert restored.has_active_slot()
    assert restored.intent_state(restored.intents()[0].logical_id).value == "PREPARED"
