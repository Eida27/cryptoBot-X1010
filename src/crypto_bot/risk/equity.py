from decimal import Decimal

from crypto_bot.domain.models import AccountSnapshot


def mark_equity(account: AccountSnapshot) -> Decimal:
    return account.wallet_balance + account.unrealized_pnl


def closing_equity(account: AccountSnapshot, exit_allowance: Decimal) -> Decimal:
    if exit_allowance < 0:
        raise ValueError("Negative exit allowance")
    return mark_equity(account) - exit_allowance
