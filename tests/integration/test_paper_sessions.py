from decimal import Decimal as D

from crypto_bot.domain.enums import Mode, OrderSide
from crypto_bot.domain.models import BookEvent, BookLevel, MarketFrame
from crypto_bot.storage.repository import make_intent


def test_gap_permanently_invalidates_forward_evidence_and_reset_is_explicit(repo, clock):
    from crypto_bot.research.paper import mark_interrupted, new_paper_session
    first = repo.current_trial()
    mark_interrupted(repo, first.run_id, "STREAM_GAP", clock.now_ms())
    repo.mark_reconciled(clock.now_ms(), True)
    repo.resume(first.run_id)
    assert repo.current_trial().qualification_status == "INTERRUPTED"
    second = new_paper_session(repo, D("20"), first.hashes, clock.now_ms())
    assert second.run_id != first.run_id
    assert repo.state()["state"] == "PAUSED"


async def test_paper_walks_captured_depth_without_inventing_liquidity(repo, clock):
    from crypto_bot.research.paper import PaperBroker
    from crypto_bot.domain.models import ApprovedSize
    from tests.fixtures.factories import signal
    broker = PaperBroker(repo, clock)
    broker.capture(BookEvent(MarketFrame("SOLUSDT", clock.now_ms(), D("99.99"), D("100"), D("100"),
        (BookLevel(D("99.99"), D("1")),), (BookLevel(D("100"), D("0.04")), BookLevel(D("101"), D("1"))),
        D("200000000"), {"quote": clock.now_ms(), "heartbeat": clock.now_ms()})))
    intent = repo.reserve_entry(signal(), ApprovedSize(D("0.08"), D("8"), D("0.1872"), D("0.2"), {"atr": "1", "limit_price": "100.05"}))
    result = await broker.submit_entry(intent)
    assert result.observation.cumulative_quantity == D("0.04")
    assert repo.fills()[0].quantity == D("0.04")


def test_paper_reset_cannot_replace_live_trial(repo, clock):
    from crypto_bot.research.paper import new_paper_session
    import pytest
    repo.db.connection.execute("UPDATE runs SET mode='LIVE'")
    with pytest.raises(ValueError, match="PAPER"):
        new_paper_session(repo, D("20"), {}, clock.now_ms())
