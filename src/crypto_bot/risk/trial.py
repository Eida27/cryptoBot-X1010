from dataclasses import dataclass
from decimal import Decimal as D

from crypto_bot.domain.models import AccountSnapshot, Trial
from crypto_bot.risk.equity import closing_equity


@dataclass(frozen=True)
class TrialDecision:
    halt: bool
    reason: str | None
    equity: D


def check_trial(trial: Trial, account: AccountSnapshot, exit_allowance: D) -> TrialDecision:
    equity = closing_equity(account, exit_allowance)
    halted = equity <= trial.floor or trial.halt_reason == "TRIAL_LOSS"
    return TrialDecision(halted, "TRIAL_LOSS" if halted else None, equity)


def validate_allocation(declared: D, wallet: D) -> None:
    if (
        not declared.is_finite()
        or not wallet.is_finite()
        or declared <= 0
        or abs(wallet - declared) > D("0.01")
    ):
        raise ValueError("Wallet must match the explicitly declared allocation within 0.01 USDT")
