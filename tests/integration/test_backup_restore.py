from decimal import Decimal as D
import pytest

from crypto_bot.domain.enums import OrderSide
from crypto_bot.domain.models import FillEvent
from crypto_bot.storage.database import Database
from crypto_bot.storage.repository import Repository, make_intent


def test_online_backup_restore_preserves_halt_and_uncertainty(repo, tmp_path):
    from crypto_bot.ops.backup import create_backup, verify_restore
    trial = repo.current_trial()
    repo.add_intent(make_intent(trial.run_id, "pending", "ENTRY", 0, "SOLUSDT", OrderSide.BUY, D("0.1"), D("100")))
    repo.record_execution(FillEvent("PAPER", "virtual", "SOLUSDT", "1", "entry", D("100"), D("0.1"), D("0.006"), "USDT", 1))
    repo.latch_halt(trial.run_id, "TRIAL_LOSS")
    manifest = create_backup(repo.db.path, tmp_path / "backups")
    restore = verify_restore(manifest, tmp_path / "restored" / "paper.sqlite3")
    assert restore.integrity == "ok"
    with pytest.raises(ValueError, match="exist"):
        verify_restore(manifest, tmp_path / "restored" / "paper.sqlite3")
    db = Database(restore.path)
    recovered = Repository(db)
    try:
        assert recovered.current_trial().floor == D("18")
        assert recovered.current_trial().halt_reason == "TRIAL_LOSS"
        assert not recovered.state()["reconciled"]
        assert not recovered.resume(trial.run_id)
        assert len(recovered.fills()) == 1
        assert recovered.has_unresolved_intent("pending")
    finally:
        db.close()


def test_tampered_backup_is_rejected_before_restore(repo, tmp_path):
    from crypto_bot.ops.backup import create_backup, verify_restore
    manifest = create_backup(repo.db.path, tmp_path / "backups")
    with manifest.path.open("ab") as file:
        file.write(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        verify_restore(manifest, tmp_path / "restored" / "paper.sqlite3")
