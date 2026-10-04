from decimal import Decimal as D

import pytest

from crypto_bot.domain.enums import Mode
from crypto_bot.domain.models import AccountSnapshot, Trial


@pytest.mark.parametrize("guard,halt", [("18.0001", False), ("18", True), ("17.9999", True)])
def test_original_floor_is_inclusive(guard, halt):
    from crypto_bot.risk.trial import check_trial

    trial = Trial("r", Mode.PAPER, D("20"), D("18"), D("50"), {})
    account = AccountSnapshot(0, D(guard), D("0"), D(guard))
    assert check_trial(trial, account, D("0")).halt is halt


def test_booked_funding_is_already_in_wallet():
    from crypto_bot.risk.equity import closing_equity, mark_equity

    account = AccountSnapshot(0, D("19.8"), D("0.1"), D("19.8"))
    assert mark_equity(account) == D("19.9")
    assert closing_equity(account, D("0.02")) == D("19.88")


def test_larger_wallet_cannot_increase_declared_allocation():
    from crypto_bot.risk.trial import validate_allocation

    with pytest.raises(ValueError, match="allocation"):
        validate_allocation(D("20"), D("200"))
    validate_allocation(D("20"), D("20.01"))
