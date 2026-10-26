"""Counterfactual search: LN-1230 amount flip, LN-1042 scorecard consistency."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from whyledger.counterfactual import find_counterfactual
from whyledger.scorecard import score
from whyledger.zetaris import StubZetarisClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"


@pytest.fixture(scope="module")
def stub() -> StubZetarisClient:
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out missing; run data/generate.py")
    return StubZetarisClient(data_dir=OUT)


def _rules(stub: StubZetarisClient) -> list[dict]:
    rows = stub.run_sql(
        "SELECT rule_id, feature, operator, threshold, points, text "
        "FROM policy_rules ORDER BY rule_id"
    ).rows
    return [dict(r) for r in rows]


def _features_for_amount(stub: StubZetarisClient, loan_id: str, amount: float) -> dict:
    from whyledger.counterfactual import fetch_loan_features

    base = fetch_loan_features(stub, loan_id)
    base["amount"] = amount
    income = base["declared_income"]
    base["amount_to_income"] = amount / income if income else 0.0
    return base


def test_ln1230_minimal_amount_reduction_flips(stub: StubZetarisClient) -> None:
    result = find_counterfactual(stub, "LN-1230")
    assert result is not None
    assert result.counterfactual.flips_to == "approve"
    assert result.queries
    assert result.counterfactual.change.startswith("reduce amount by")
    match = re.search(r"reduce amount by (\d+)%", result.counterfactual.change)
    assert match is not None
    pct = int(match.group(1))
    rules = _rules(stub)
    app = stub.run_sql(
        "SELECT amount FROM applications WHERE loan_id = 'LN-1230'"
    ).rows[0]
    amount = float(app["amount"])
    at_pct = amount * (1.0 - pct / 100.0)
    below = amount * (1.0 - (pct - 1) / 100.0) if pct > 1 else amount
    assert score(_features_for_amount(stub, "LN-1230", below), rules).outcome == "decline"
    assert score(_features_for_amount(stub, "LN-1230", at_pct), rules).outcome == "approve"


def test_ln1042_counterfactual_matches_scorecard(stub: StubZetarisClient) -> None:
    result = find_counterfactual(stub, "LN-1042")
    assert result is not None
    assert result.counterfactual.flips_to == "approve"
    from whyledger.counterfactual import fetch_loan_features, load_policy_rules

    features = fetch_loan_features(stub, "LN-1042")
    rules = load_policy_rules(stub)
    assert score(features, rules).outcome == "decline"
    # Re-run search logic: chosen change must flip when applied to one feature.
    change = result.counterfactual.change
    assert "late payments over 15 days" in change
    m = re.search(r"to (\d+)$", change)
    assert m is not None
    trial = dict(features)
    trial["late_over_15_of_last_6"] = float(m.group(1))
    assert score(trial, rules).outcome == "approve"


def test_approved_loan_returns_none(stub: StubZetarisClient) -> None:
    assert find_counterfactual(stub, "LN-1107") is None


def test_ln1301_bureau_score_flip(stub: StubZetarisClient) -> None:
    result = find_counterfactual(stub, "LN-1301")
    assert result is not None
    assert result.counterfactual.flips_to == "approve"
    assert "bureau score" in result.counterfactual.change.lower()
