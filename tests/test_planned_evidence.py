"""Planned evidence column labelling (scorecard vs bureau)."""

from __future__ import annotations

from whyledger.planned_evidence import gather_planned_evidence
from whyledger.zetaris import StubZetarisClient


def test_planned_evidence_aliases_scorecard_points_not_credit_score() -> None:
    client = StubZetarisClient()
    items = gather_planned_evidence(client, "LN-1042")
    by_id = {it.evidence_id: it for it in items}
    app = by_id["ev_application"]
    assert "scorecard_points" in app.columns
    assert "score" not in app.columns
    assert float(app.rows[0]["scorecard_points"]) == 105.0

    bureau = by_id["ev_bureau"]
    assert "bureau_score" in bureau.columns
    assert float(bureau.rows[0]["bureau_score"]) == 670

    peer = by_id["ev_peer_cases"]
    assert "scorecard_points" in peer.columns
    assert "score" not in peer.columns


def test_planned_draft_glossary_distinguishes_bureau() -> None:
    from whyledger.prompts import planned_draft_field_glossary

    text = planned_draft_field_glossary()
    assert "scorecard_points" in text
    assert "bureau_score" in text
    assert "credit score" in text.lower()
    assert "aliases only" in text.lower() or "display labels" in text.lower()
    assert "bureau.score" in text or "bureau: applicant_id, score" in text
    assert "not real column" in text.lower() or "not real column names" in text.lower()
