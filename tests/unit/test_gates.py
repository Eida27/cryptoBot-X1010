from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.config import STRATEGY_HASH, Settings


def evidence(settings):
    from crypto_bot.research.gates import EvidenceBundle, code_hash

    return EvidenceBundle(
        {
            "config": settings.config_hash,
            "strategy": STRATEGY_HASH,
            "code": code_hash(),
            "data": "dataset",
        },
        settings.initial_capital_usdt,
    )


def test_missing_evidence_is_never_pass():
    from crypto_bot.research.gates import evaluate_gates

    settings = Settings()
    report = evaluate_gates(evidence(settings), settings)
    assert all(gate.status.value == "NOT_YET_OBSERVED" for gate in report.gates)


def test_historical_counts_and_actual_funding_are_required_in_both_cost_cases():
    from crypto_bot.research.gates import HistoricalEvidence, evaluate_gates

    settings = Settings()
    bundle = evidence(settings)
    base = HistoricalEvidence(49, D("1"), D("0.05"), True, True, False, False, 24, bundle.hashes)
    stress = replace(base, closed_trades=50)
    report = evaluate_gates(
        replace(bundle, historical_base=base, historical_stress=stress), settings
    )
    assert report.by_id("G3").status.value == "NOT_YET_OBSERVED"
    report = evaluate_gates(
        replace(
            bundle,
            historical_base=replace(base, closed_trades=50, actual_funding=False),
            historical_stress=stress,
        ),
        settings,
    )
    assert report.by_id("G3").status.value == "FAIL"


def test_29_days_or_19_trades_cannot_pass_forward_observation():
    from crypto_bot.research.gates import PaperEvidence, evaluate_gates

    settings = Settings()
    bundle = evidence(settings)
    paper = PaperEvidence(29 * 86400000, 20, D("1"), D("0.1"), 0, False, False, bundle.hashes)
    assert (
        evaluate_gates(replace(bundle, paper=paper), settings).by_id("G5").status.value
        == "NOT_YET_OBSERVED"
    )
    paper = replace(paper, observed_ms=30 * 86400000, closed_trades=19)
    assert (
        evaluate_gates(replace(bundle, paper=paper), settings).by_id("G5").status.value
        == "NOT_YET_OBSERVED"
    )
    paper = replace(paper, closed_trades=20, interruptions=1)
    assert evaluate_gates(replace(bundle, paper=paper), settings).by_id("G5").status.value == "FAIL"


def test_hash_mismatch_invalidates_all_existing_evidence():
    from crypto_bot.research.gates import evaluate_gates

    settings = Settings()
    bundle = evidence(settings)
    bundle = replace(bundle, hashes={**bundle.hashes, "config": "changed"}, logic_verified=True)
    assert evaluate_gates(bundle, settings).by_id("G1").status.value == "FAIL"


def test_negative_after_hosting_blocks_economic_review():
    from crypto_bot.research.gates import PaperEvidence, evaluate_gates

    settings = Settings()
    bundle = evidence(settings)
    paper = PaperEvidence(30 * 86400000, 20, D("1"), D("-5"), 0, False, False, bundle.hashes)
    assert (
        evaluate_gates(replace(bundle, paper=paper, operations={"verified": True}), settings)
        .by_id("G6")
        .status.value
        == "FAIL"
    )
