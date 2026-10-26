"""Tests for WhyCard builder: verified-only claims, synthetic flag, letter bans."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from whyledger.card import (
    CardClaim,
    Driver,
    WhyCard,
    aggregate_drivers_by_feature,
    build_card,
    render_applicant_letter,
)
from whyledger.ledger import Claim, Ledger
from whyledger.verifier import VerificationResult, verify_claims
from whyledger.zetaris import StubZetarisClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"

LATE_SQL = (
    "SELECT COUNT(*) AS late_count FROM ("
    " SELECT late_days FROM payments WHERE loan_id = 'LN-0900'"
    " ORDER BY due_date DESC LIMIT 6"
    ") t WHERE CAST(late_days AS INTEGER) > 15"
)
INCOME_SQL = (
    "SELECT CAST(declared_income AS REAL) AS declared_income"
    " FROM applications WHERE loan_id = 'LN-1042'"
)


@pytest.fixture(scope="module")
def stub() -> StubZetarisClient:
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out/applications.csv missing; run data/generate.py first")
    return StubZetarisClient(data_dir=OUT)


def test_applicant_letter_bans_legally_compliant_guarantee() -> None:
    letter = render_applicant_letter(
        loan_id="LN-1042",
        decision="decline",
        drivers=[
            Driver(rank=1, feature="late_over_15_of_last_6", weight_pct=47.5, claim_id="c1"),
        ],
        claims=[
            CardClaim(
                id="c1",
                text="3 late payments on prior loan over 15 days",
                query_ids=[],
            )
        ],
    )
    lower = letter.lower()
    for word in ("legally", "compliant", "guarantee"):
        assert word not in lower
    assert "SYNTHETIC" in letter
    assert "Principal reasons" in letter


def test_whycard_rejects_banned_letter_words() -> None:
    with pytest.raises(ValidationError):
        WhyCard(
            decision="decline",
            mode="stub",
            applicant_letter="This is legally fine and compliant with a guarantee.",
            verified_at="2026-01-01T00:00:00+00:00",
            model="test",
            synthetic=True,
        )


def test_build_card_rejects_unsupported_keeps_verified(stub: StubZetarisClient) -> None:
    late = stub.run_sql(LATE_SQL)
    income = stub.run_sql(INCOME_SQL)
    ledger = Ledger()
    ledger.append_evidence(
        query_id=late.query_id, sql=late.sql, columns=late.columns, rows=late.rows
    )
    ledger.append_evidence(
        query_id=income.query_id,
        sql=income.sql,
        columns=income.columns,
        rows=income.rows,
    )
    true_claim = Claim(
        id="c-late",
        text="3 of last 6 payments on prior loan LN-0900 were over 15 days late",
        values={"late_count": 3},
        query_ids=[late.query_id],
    )
    false_claim = Claim(
        id="c-income-lie",
        text="declared income below 20000",
        values={"declared_income": 20000},
        query_ids=[income.query_id],
    )
    ledger.append_claim(true_claim)
    ledger.append_claim(false_claim)
    results = verify_claims(
        [true_claim, false_claim], ledger, stub, subject_loan_id="LN-1042"
    )

    card = build_card(
        decision="decline",
        mode="stub",
        claims=[true_claim, false_claim],
        results=results,
        ledger=ledger,
        drivers=[
            Driver(rank=1, feature="late_over_15_of_last_6", weight_pct=40.0, claim_id="c-late")
        ],
        loan_id="LN-1042",
        model="test-model",
        verified_at="2026-10-22T00:00:00+00:00",
    )

    assert card.synthetic is True
    assert all(c.status == "VERIFIED" for c in card.claims)
    claim_ids = {c.id for c in card.claims}
    claim_texts = {c.text for c in card.claims}
    assert "c-late" in claim_ids
    assert "c-income-lie" not in claim_ids
    assert "declared income below 20000" not in claim_texts
    assert any("declared income below 20000" == r.text for r in card.rejected)
    assert any("does not match column" in r.reason for r in card.rejected)
    for word in ("legally", "compliant", "guarantee"):
        assert word not in card.applicant_letter.lower()


def test_rejected_never_in_claims_even_if_listed() -> None:
    """build_card must not put UNSUPPORTED results into claims."""
    ledger = Ledger()
    claim = Claim(id="x", text="unsupported story", values={"n": 1}, query_ids=[])
    ledger.append_claim(claim)
    results = [
        VerificationResult(claim_id="x", status="UNSUPPORTED", reason="no query evidence cited")
    ]
    card = build_card(
        decision="decline",
        mode="replay",
        claims=[claim],
        results=results,
        ledger=ledger,
        loan_id="LN-1042",
        model="none",
        verified_at="2026-10-22T00:00:00+00:00",
    )
    assert card.claims == []
    assert len(card.rejected) == 1
    assert card.rejected[0].text == "unsupported story"
    assert card.synthetic is True


def test_aggregate_drivers_by_feature_merges_duplicate_features() -> None:
    raw = [
        Driver(rank=1, feature="amount_to_income", weight_pct=41.1, claim_id=None),
        Driver(rank=2, feature="utilisation", weight_pct=20.5, claim_id="c2"),
        Driver(rank=4, feature="amount_to_income", weight_pct=11.0, claim_id=None),
    ]
    merged = aggregate_drivers_by_feature(raw)
    assert len(merged) == 2
    assert merged[0].feature == "amount_to_income"
    assert merged[0].weight_pct == 52.1
    total = sum(d.weight_pct for d in merged)
    assert abs(total - 72.6) < 0.05
