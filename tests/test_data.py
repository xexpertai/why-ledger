"""Tests for SYNTHETIC Harbor Credit dataset and scorecard consistency."""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "out"
TRUTH = DATA / "truth.json"

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(DATA))

from generate import (  # noqa: E402
    INCOME_LOW_THRESHOLD,
    N_APPLICATIONS,
    N_BUREAU,
    SEED,
    dataset_checksum,
    generate,
    policy_rules,
)
from whyledger.scorecard import (  # noqa: E402
    AMOUNT_TO_INCOME_DEBT_SERVICE,
    APPROVE_THRESHOLD,
    BASE_SCORE,
    RATE_POINTS_PER_DEFAULT,
    score,
)


def _ensure_dataset() -> None:
    if not (OUT / "applications.csv").exists() or not TRUTH.exists():
        generate(SEED)


@pytest.fixture(scope="module")
def dataset():
    _ensure_dataset()
    apps = {}
    with (OUT / "applications.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            apps[row["loan_id"]] = row
    bureau = json.loads((OUT / "bureau.json").read_text(encoding="utf-8"))
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    rules = list(csv.DictReader((OUT / "policy_rules.csv").open(encoding="utf-8")))
    payments_path = OUT / "payments.csv"
    if not payments_path.exists():
        payments_path = OUT / "payments.parquet"
    return {
        "apps": apps,
        "bureau": bureau,
        "truth": truth,
        "rules": rules,
        "payments_path": payments_path,
    }


def test_row_counts_and_synthetic_labels(dataset):
    assert len(dataset["apps"]) == N_APPLICATIONS
    assert len(dataset["bureau"]["rows"]) == N_BUREAU
    assert 20 <= len(dataset["rules"]) <= 30
    assert dataset["bureau"]["is_synthetic"] is True
    for row in dataset["apps"].values():
        assert row["is_synthetic"] in ("True", "true", True) or row["is_synthetic"] == "True"
        assert row["lender"] == "Harbor Credit"
        assert "SYNTHETIC" in row["note"]
    for rule in dataset["rules"]:
        assert rule["is_synthetic"] in ("True", "true", True) or rule["is_synthetic"] == "True"
    assert dataset["payments_path"].exists()
    docs = (OUT / "policy_docs.md").read_text(encoding="utf-8")
    assert "SYNTHETIC" in docs
    assert "Harbor Credit" in docs
    chunks = json.loads((OUT / "policy_docs_chunks.json").read_text(encoding="utf-8"))
    assert len(chunks) >= 10


def test_ln_1042_planted_facts(dataset):
    app = dataset["apps"]["LN-1042"]
    truth = dataset["truth"]["planted"]["LN-1042"]
    assert app["outcome"] == "decline"
    assert truth["outcome"] == "decline"
    assert float(app["declared_income"]) >= INCOME_LOW_THRESHOLD
    assert truth["facts"]["declared_income_above_policy_threshold"] is True
    assert truth["facts"]["utilisation"] == 91
    assert truth["facts"]["late_of_last_6_over_15_days"] == 3
    assert "low_income" in truth["false_stories"]

    ap = app["applicant_id"]
    bmap = {r["applicant_id"]: r for r in dataset["bureau"]["rows"]}
    assert bmap[ap]["utilisation"] == 91

    # Prior loan payments: 3 of last 6 over 15 days.
    prior = truth["facts"]["prior_loan_id"]
    pays = []
    with dataset["payments_path"].open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["loan_id"] == prior and row["applicant_id"] == ap:
                pays.append(row)
    assert len(pays) >= 6
    last6 = pays[-6:]
    late = sum(1 for p in last6 if int(p["late_days"]) > 15)
    assert late == 3


def test_ln_1107_pricing_and_bureau_defaults(dataset):
    app = dataset["apps"]["LN-1107"]
    truth = dataset["truth"]["planted"]["LN-1107"]
    first_id = truth["pricing"]["first_loan_id"]
    first = dataset["apps"][first_id]
    assert app["applicant_id"] == first["applicant_id"]
    assert float(app["rate"]) - float(first["rate"]) == pytest.approx(4.0)
    assert truth["pricing"]["rate_delta_points"] == 4.0
    assert truth["facts"]["defaults"] == 2
    assert truth["facts"]["defaults_source"] == "bureau_only"
    bmap = {r["applicant_id"]: r for r in dataset["bureau"]["rows"]}
    assert bmap[app["applicant_id"]]["defaults"] == 2
    # Fragmentation: applications table has no defaults column.
    assert "defaults" not in app


def test_truth_json_has_planted_keyed_by_loan_id(dataset):
    truth = dataset["truth"]
    assert "planted" in truth
    planted = truth["planted"]
    assert isinstance(planted, dict)
    for loan_id in ("LN-1042", "LN-1107", "LN-1230", "LN-1301"):
        assert loan_id in planted
        assert planted[loan_id]["outcome"] in ("approve", "decline")


def test_ln_1230_amount_counterfactual(dataset):
    """LN-1230: borderline decline; amount*0.85 flips to approve (debt-service R08)."""
    app = dataset["apps"]["LN-1230"]
    truth = dataset["truth"]["planted"]["LN-1230"]
    assert app["outcome"] == "decline"
    assert truth["outcome"] == "decline"
    amount = float(app["amount"])
    income = float(app["declared_income"])
    reduced = amount * 0.85
    assert amount / income > AMOUNT_TO_INCOME_DEBT_SERVICE
    assert reduced / income <= AMOUNT_TO_INCOME_DEBT_SERVICE
    assert truth["counterfactual"]["flips_to"] == "approve"
    assert truth["counterfactual"]["change"] == "reduce amount by 15%"

    rules = [
        {
            "rule_id": r["rule_id"],
            "feature": r["feature"],
            "operator": r["operator"],
            "threshold": float(r["threshold"]),
            "points": float(r["points"]),
            "text": r["text"],
        }
        for r in dataset["rules"]
    ]
    bmap = {r["applicant_id"]: r for r in dataset["bureau"]["rows"]}
    b = bmap[app["applicant_id"]]

    def features(amt: float) -> dict:
        sector = app["sector"]
        sector_risk = 2 if sector in ("hospitality", "construction") else (
            1 if sector in ("retail", "transport") else 0
        )
        return {
            "amount": amt,
            "term": float(app["term"]),
            "years_in_business": float(app["years_in_business"]),
            "declared_income": income,
            "utilisation": float(b["utilisation"]),
            "bureau_score": float(b["score"]),
            "enquiries_6m": float(b["enquiries_6m"]),
            "defaults": float(b["defaults"]),
            "late_over_15_of_last_6": 0.0,
            "amount_to_income": amt / income,
            "prior_loans": float(app["prior_loans"]),
            "sector_risk": float(sector_risk),
        }

    current = score(features(amount), rules)
    flipped = score(features(reduced), rules)
    assert current.outcome == "decline"
    assert flipped.outcome == "approve"
    assert current.score < APPROVE_THRESHOLD
    assert flipped.score >= APPROVE_THRESHOLD
    # All other features unchanged: only amount (and derived amount_to_income) differ.
    assert features(amount).keys() == features(reduced).keys()
    for key in features(amount):
        if key in ("amount", "amount_to_income"):
            continue
        assert features(amount)[key] == features(reduced)[key]


def test_ln_1301_reason_contradicts_data(dataset):
    app = dataset["apps"]["LN-1301"]
    truth = dataset["truth"]["planted"]["LN-1301"]
    assert app["outcome"] == "decline"
    assert app["reason_code"] == truth["recorded_reason_code"]
    assert truth["recorded_reason_supported"] is False
    assert "recorded reason not supported" in truth["agent_should_flag"]
    assert float(app["declared_income"]) >= INCOME_LOW_THRESHOLD
    assert truth["facts"]["declared_income_above_policy_threshold"] is True
    # Recorded reason claims income, but income is strong.
    assert "INCOME" in app["reason_code"]


def test_scorecard_consistent_with_application_outcomes(dataset):
    """Spot-check planted loans: recomputed scorecard matches stored outcome/rate."""
    rules = [
        {
            "rule_id": r["rule_id"],
            "feature": r["feature"],
            "operator": r["operator"],
            "threshold": float(r["threshold"]),
            "points": float(r["points"]),
            "text": r["text"],
        }
        for r in dataset["rules"]
    ]
    bmap = {r["applicant_id"]: r for r in dataset["bureau"]["rows"]}
    truth = dataset["truth"]["planted"]

    # LN-1042
    app = dataset["apps"]["LN-1042"]
    b = bmap[app["applicant_id"]]
    feat = {
        "amount": float(app["amount"]),
        "term": float(app["term"]),
        "years_in_business": float(app["years_in_business"]),
        "declared_income": float(app["declared_income"]),
        "utilisation": float(b["utilisation"]),
        "bureau_score": float(b["score"]),
        "enquiries_6m": float(b["enquiries_6m"]),
        "defaults": float(b["defaults"]),
        "late_over_15_of_last_6": float(
            truth["LN-1042"]["facts"]["late_of_last_6_over_15_days"]
        ),
        "amount_to_income": float(app["amount"]) / float(app["declared_income"]),
        "prior_loans": float(app["prior_loans"]),
        "sector_risk": 0.0,
    }
    result = score(feat, rules)
    assert result.outcome == app["outcome"]
    assert result.score == pytest.approx(float(app["score"]))
    assert result.rate == pytest.approx(float(app["rate"]))
    # Low-income rule must not fire.
    assert not any(h.rule_id == "R01" and h.fired for h in result.rule_hits)

    # LN-1107 rate uplift from defaults
    assert RATE_POINTS_PER_DEFAULT * 2 == pytest.approx(4.0)


def test_policy_rules_match_module():
    built = policy_rules()
    assert len(built) == 25
    assert built[0]["rule_id"] == "R01"
    assert float(built[0]["threshold"]) == INCOME_LOW_THRESHOLD


def test_determinism_two_runs_same_checksum(tmp_path):
    """Two fresh generations with seed 2026 produce the same checksum."""
    # Run in isolated copies of out/ by temporarily swapping via generate's paths.
    # generate() always writes to data/out and data/truth.json — snapshot checksums.
    generate(SEED)
    checksum_a = dataset_checksum()
    # Wipe and regenerate.
    if OUT.exists():
        shutil.rmtree(OUT)
    if TRUTH.exists():
        TRUTH.unlink()
    generate(SEED)
    checksum_b = dataset_checksum()
    assert checksum_a == checksum_b
    assert len(checksum_a) == 64


def test_scorecard_base_constants():
    rules = [
        {
            "rule_id": "T1",
            "feature": "defaults",
            "operator": ">=",
            "threshold": 1,
            "points": -10,
            "text": "test",
        }
    ]
    result = score({"defaults": 0}, rules)
    assert result.score == BASE_SCORE
    assert result.outcome == "approve"
    assert result.is_synthetic is True
