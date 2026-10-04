from decimal import Decimal as D

from crypto_bot.domain.enums import Mode, PositionSide
from crypto_bot.domain.models import Position
from crypto_bot.research.simulation import ClosedTrade, RunResult


def test_metrics_separate_booked_costs_and_prorated_hosting():
    from crypto_bot.research.reports import HostingCost, build_report

    position = Position("SOLUSDT", PositionSide.LONG, D("1"), D("100"), D("1"), 0)
    trade = ClosedTrade(
        position, 86400000, D("102"), D("2"), D("0.12"), D("-0.02"), D("1.86"), "TARGET"
    )
    run = RunResult(
        "r",
        Mode.PAPER,
        D("20"),
        0,
        15 * 86400000,
        {},
        (trade,),
        ((0, D("20")), (1, D("22")), (2, D("20.9"))),
        (),
        (),
        (),
        (),
        (),
    )
    report = build_report(run, HostingCost(D("6")))
    assert report.metrics["gross_pnl"] == D("2")
    assert report.metrics["net_trading_pnl"] == D("1.86")
    assert report.metrics["hosting_usdt"] == D("3")
    assert report.metrics["after_hosting_pnl"] == D("-1.14")
    assert report.metrics["max_drawdown"] == D("0.05")
    assert "credentials" not in report.json_text
