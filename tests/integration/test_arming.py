from decimal import Decimal as D
import pytest

from crypto_bot.config import Settings
from crypto_bot.domain.models import AccountSnapshot, ExchangeSnapshot
from tests.unit.test_gates import evidence


def test_flags_alone_cannot_arm_live_trial(tmp_path):
    from crypto_bot.research.gates import arm_trial
    settings = Settings(mode="LIVE", host_profile="vps", live_trading_enabled=True,
                        database=tmp_path / "live.sqlite3", initial_capital_usdt=D("20"))
    with pytest.raises(ValueError, match="G1"):
        arm_trial(evidence(settings), settings, D("20"), "ARM NEW BOUNDED LIVE TRIAL")
    assert not settings.database.exists()


def test_arming_cannot_infer_allocation_from_large_wallet(tmp_path):
    from crypto_bot.research.gates import validate_arming_preflight
    settings = Settings(mode="LIVE", host_profile="vps", live_trading_enabled=True,
                        database=tmp_path / "live.sqlite3", initial_capital_usdt=D("20"))
    snapshot = ExchangeSnapshot(0, AccountSnapshot(0, D("200"), D("0"), D("200")))
    with pytest.raises(ValueError, match="allocation"):
        validate_arming_preflight(settings, D("20"), snapshot)


def test_renaming_a_loss_halted_run_cannot_resume_it(repo):
    repo.latch_halt(repo.current_trial().run_id, "TRIAL_LOSS")
    repo.db.connection.execute("UPDATE runs SET qualification_status='NEW_TRIAL'")
    repo.mark_reconciled(0, True)
    assert not repo.resume(repo.current_trial().run_id)
    assert repo.current_trial().floor == D("18")
