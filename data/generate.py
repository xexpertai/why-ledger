#!/usr/bin/env python3
"""Generate Harbor Credit SYNTHETIC demo dataset (seed=2026, deterministic).

Stdlib only. Payments prefer Parquet when pyarrow is installed; otherwise CSV
with an explicit console note. Outputs land in data/out/ (gitignored).
Ground-truth drivers for planted loans are written to data/truth.json (tests
only — never feed this file to the agent).
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow `python data/generate.py` without installing editable when needed.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from whyledger.scorecard import (  # noqa: E402
    AMOUNT_TO_INCOME_DEBT_SERVICE,
    APPROVE_THRESHOLD,
    BASE_RATE_PCT,
    BASE_SCORE,
    LENDER_NAME,
    RATE_POINTS_PER_DEFAULT,
    score as scorecard_score,
)

SEED = 2026
N_APPLICATIONS = 2000
N_BUREAU = 1400
N_PAYMENTS_TARGET = 30_000

OUT_DIR = Path(__file__).resolve().parent / "out"
TRUTH_PATH = Path(__file__).resolve().parent / "truth.json"

# Income below this fires the low-income rule (LN-1042 must sit ABOVE it).
INCOME_LOW_THRESHOLD = 40_000.0

SECTORS = (
    "retail",
    "hospitality",
    "services",
    "construction",
    "healthcare",
    "technology",
    "manufacturing",
    "transport",
)

FIRST_NAMES = (
    "Asha", "Blair", "Casey", "Devon", "Ellis", "Frankie", "Gray", "Harper",
    "Indigo", "Jules", "Kai", "Logan", "Morgan", "Noor", "Oakley", "Parker",
    "Quinn", "Remy", "Sage", "Tatum", "Uma", "Vale", "Winter", "Yael", "Zion",
)
LAST_NAMES = (
    "Alden", "Brooks", "Chen", "Dalton", "Everett", "Farley", "Gomez", "Hayes",
    "Ingram", "Jordan", "Kline", "Larsen", "Mendez", "Nash", "Owen", "Patel",
    "Quincy", "Ramos", "Singh", "Tran", "Underwood", "Vega", "Walsh", "Xu", "Young",
)


def policy_rules() -> list[dict]:
    """Fixed ~25 Harbor Credit scorecard rules (SYNTHETIC)."""
    rules: list[dict] = [
        {
            "rule_id": "R01",
            "feature": "declared_income",
            "operator": "<",
            "threshold": INCOME_LOW_THRESHOLD,
            "points": -35,
            "text": "Declared income below Harbor Credit minimum self-employed threshold",
        },
        {
            "rule_id": "R02",
            "feature": "utilisation",
            "operator": ">=",
            "threshold": 90,
            "points": -40,
            "text": "Credit utilisation at or above 90 percent",
        },
        {
            "rule_id": "R03",
            "feature": "utilisation",
            "operator": ">=",
            "threshold": 70,
            "points": -15,
            "text": "Credit utilisation at or above 70 percent",
        },
        {
            "rule_id": "R04",
            "feature": "late_over_15_of_last_6",
            "operator": ">=",
            "threshold": 3,
            "points": -45,
            "text": "Three or more of last six payments late by over 15 days",
        },
        {
            "rule_id": "R05",
            "feature": "late_over_15_of_last_6",
            "operator": ">=",
            "threshold": 1,
            "points": -12,
            "text": "At least one of last six payments late by over 15 days",
        },
        {
            "rule_id": "R06",
            "feature": "defaults",
            "operator": ">=",
            "threshold": 2,
            "points": -30,
            "text": "Two or more bureau defaults on file",
        },
        {
            "rule_id": "R07",
            "feature": "defaults",
            "operator": ">=",
            "threshold": 1,
            "points": -18,
            "text": "One or more bureau defaults on file",
        },
        {
            "rule_id": "R08",
            "feature": "amount_to_income",
            "operator": ">",
            "threshold": AMOUNT_TO_INCOME_DEBT_SERVICE,
            "points": -30,
            "text": (
                "Requested amount exceeds 2.5 times declared income "
                "(debt-service pressure)"
            ),
        },
        {
            "rule_id": "R09",
            "feature": "amount",
            "operator": ">",
            "threshold": 250_000,
            "points": -20,
            "text": "Requested amount above standard single-facility cap",
        },
        {
            "rule_id": "R10",
            "feature": "bureau_score",
            "operator": "<",
            "threshold": 580,
            "points": -40,
            "text": "Bureau score below Harbor Credit floor",
        },
        {
            "rule_id": "R11",
            "feature": "bureau_score",
            "operator": "<",
            "threshold": 650,
            "points": -15,
            "text": "Bureau score below preferred band",
        },
        {
            "rule_id": "R12",
            "feature": "bureau_score",
            "operator": ">=",
            "threshold": 750,
            "points": 10,
            "text": "Bureau score in strong band",
        },
        {
            "rule_id": "R13",
            "feature": "enquiries_6m",
            "operator": ">=",
            "threshold": 6,
            "points": -25,
            "text": "Six or more bureau enquiries in six months",
        },
        {
            "rule_id": "R14",
            "feature": "enquiries_6m",
            "operator": ">=",
            "threshold": 4,
            "points": -10,
            "text": "Four or more bureau enquiries in six months",
        },
        {
            "rule_id": "R15",
            "feature": "years_in_business",
            "operator": "<",
            "threshold": 2,
            "points": -20,
            "text": "Business operating less than two years",
        },
        {
            "rule_id": "R16",
            "feature": "years_in_business",
            "operator": ">=",
            "threshold": 5,
            "points": 8,
            "text": "Business operating five years or more",
        },
        {
            "rule_id": "R17",
            "feature": "term",
            "operator": ">",
            "threshold": 48,
            "points": -10,
            "text": "Requested term longer than 48 months",
        },
        {
            "rule_id": "R18",
            "feature": "term",
            "operator": "<=",
            "threshold": 24,
            "points": 5,
            "text": "Requested term 24 months or shorter",
        },
        {
            "rule_id": "R19",
            "feature": "amount_to_income",
            "operator": ">",
            "threshold": 3.0,
            "points": -22,
            "text": "Amount exceeds three times declared annual income",
        },
        {
            "rule_id": "R20",
            "feature": "amount_to_income",
            "operator": ">",
            "threshold": 2.0,
            "points": -8,
            "text": "Amount exceeds two times declared annual income",
        },
        {
            "rule_id": "R21",
            "feature": "prior_loans",
            "operator": ">=",
            "threshold": 1,
            "points": 4,
            "text": "Repeat Harbor Credit borrower",
        },
        {
            "rule_id": "R22",
            "feature": "sector_risk",
            "operator": ">=",
            "threshold": 2,
            "points": -12,
            "text": "Higher-risk sector classification",
        },
        {
            "rule_id": "R23",
            "feature": "sector_risk",
            "operator": "==",
            "threshold": 0,
            "points": 5,
            "text": "Lower-risk sector classification",
        },
        {
            "rule_id": "R24",
            "feature": "declared_income",
            "operator": ">=",
            "threshold": 100_000,
            "points": 6,
            "text": "Declared income in strong band",
        },
        {
            "rule_id": "R25",
            "feature": "utilisation",
            "operator": "<",
            "threshold": 30,
            "points": 5,
            "text": "Credit utilisation below 30 percent",
        },
    ]
    for r in rules:
        r["is_synthetic"] = True
        r["lender"] = LENDER_NAME
    return rules


def sector_risk(sector: str) -> int:
    if sector in ("hospitality", "construction"):
        return 2
    if sector in ("retail", "transport"):
        return 1
    return 0


def synthetic_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def loan_id_for(index: int) -> str:
    return f"LN-{index:04d}"


def applicant_id_for(index: int) -> str:
    return f"AP-{index:04d}"


def build_features(
    *,
    amount: float,
    term: int,
    sector: str,
    years_in_business: float,
    declared_income: float,
    utilisation: float,
    bureau_score: float,
    enquiries_6m: int,
    defaults: int,
    late_over_15_of_last_6: int,
    prior_loans: int,
) -> dict:
    income = max(declared_income, 1.0)
    return {
        "amount": float(amount),
        "term": float(term),
        "years_in_business": float(years_in_business),
        "declared_income": float(declared_income),
        "utilisation": float(utilisation),
        "bureau_score": float(bureau_score),
        "enquiries_6m": float(enquiries_6m),
        "defaults": float(defaults),
        "late_over_15_of_last_6": float(late_over_15_of_last_6),
        "amount_to_income": float(amount) / income,
        "prior_loans": float(prior_loans),
        "sector_risk": float(sector_risk(sector)),
    }


def decide(features: dict, rules: list[dict]) -> dict:
    result = scorecard_score(features, rules)
    fired_clean = []
    for h in result.rule_hits:
        if not h.fired:
            continue
        fired_clean.append(
            {
                "rule_id": h.rule_id,
                "feature": h.feature,
                "points": float(
                    next(r["points"] for r in rules if r["rule_id"] == h.rule_id)
                ),
                "text": h.text,
            }
        )
    return {
        "score": result.score,
        "outcome": result.outcome,
        "rate": result.rate,
        "fired_rules": fired_clean,
    }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_payments(path_parquet: Path, path_csv: Path, rows: list[dict]) -> str:
    """Write payments as Parquet if pyarrow is available, else CSV. Returns format used."""
    fieldnames = [
        "applicant_id",
        "loan_id",
        "due_date",
        "paid_date",
        "amount",
        "late_days",
        "is_synthetic",
        "lender",
        "note",
    ]
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        write_csv(path_csv, rows, fieldnames)
        print(
            "SYNTHETIC note: pyarrow not installed; wrote payments as CSV "
            f"({path_csv.name}) instead of Parquet."
        )
        return "csv"

    table = pa.Table.from_pylist(rows)
    path_parquet.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path_parquet)
    print(f"Wrote payments Parquet ({path_parquet.name}).")
    return "parquet"


def policy_doc_chunks() -> list[dict]:
    """~12 markdown policy chunks for Harbor Credit (SYNTHETIC)."""
    chunks = [
        {
            "doc_id": "PD-01",
            "section": "purpose",
            "text": (
                "# Harbor Credit — Small Business Credit Policy (SYNTHETIC)\n\n"
                "This document is fictional demo policy for Harbor Credit. "
                "Every figure and rule is synthetic (is_synthetic=true)."
            ),
        },
        {
            "doc_id": "PD-02",
            "section": "income_threshold",
            "text": (
                "## Declared income\n\n"
                f"Applications with declared annual income below "
                f"{int(INCOME_LOW_THRESHOLD)} attract rule R01 (low income). "
                "Income at or above the threshold does not fire R01."
            ),
        },
        {
            "doc_id": "PD-03",
            "section": "utilisation",
            "text": (
                "## Credit utilisation\n\n"
                "Utilisation at or above 90% is a severe negative driver (R02). "
                "Utilisation at or above 70% is a moderate driver (R03)."
            ),
        },
        {
            "doc_id": "PD-04",
            "section": "late_payments",
            "text": (
                "## Payment conduct\n\n"
                "If 3 or more of the last 6 instalments on a prior Harbor Credit "
                "facility were paid more than 15 days late, rule R04 applies."
            ),
        },
        {
            "doc_id": "PD-05",
            "section": "defaults_pricing",
            "text": (
                "## Bureau defaults and pricing\n\n"
                f"Interest rate starts at {BASE_RATE_PCT:.1f}% and adds "
                f"{RATE_POINTS_PER_DEFAULT:.1f} points per bureau default. "
                "Two defaults therefore price a facility 4 points higher than "
                "an otherwise identical zero-default file."
            ),
        },
        {
            "doc_id": "PD-06",
            "section": "debt_service",
            "text": (
                "## Debt-service / amount-to-income\n\n"
                f"When requested amount divided by declared income exceeds "
                f"{AMOUNT_TO_INCOME_DEBT_SERVICE}, rule R08 applies a debt-service "
                "penalty. Reducing a borderline request by 15% can drop the ratio "
                "below this band and flip a decline to approve."
            ),
        },
        {
            "doc_id": "PD-07",
            "section": "bureau_score",
            "text": (
                "## Bureau score bands\n\n"
                f"Base scorecard starts at {BASE_SCORE}; approve when total "
                f">= {APPROVE_THRESHOLD}. Bureau score below 580 fires R10; "
                "below 650 fires R11; at or above 750 earns a small positive R12."
            ),
        },
        {
            "doc_id": "PD-08",
            "section": "enquiries",
            "text": (
                "## Enquiries\n\n"
                "Four or more bureau enquiries in six months fire R14; six or "
                "more fire the stronger R13."
            ),
        },
        {
            "doc_id": "PD-09",
            "section": "years_in_business",
            "text": (
                "## Time in business\n\n"
                "Less than two years is a negative driver (R15). Five or more "
                "years earns a modest positive (R16)."
            ),
        },
        {
            "doc_id": "PD-10",
            "section": "amount_to_income",
            "text": (
                "## Amount relative to income (secondary bands)\n\n"
                "Amount above 2x declared income fires R20; above 3x fires R19. "
                f"The primary debt-service band at "
                f"{AMOUNT_TO_INCOME_DEBT_SERVICE}x is R08 (see debt_service)."
            ),
        },
        {
            "doc_id": "PD-11",
            "section": "reason_codes",
            "text": (
                "## Recorded reason codes\n\n"
                "Operations may store a reason_code on the decision record. "
                "If a recorded code contradicts scorecard evidence, treat it as "
                "unsupported and flag 'recorded reason not supported'."
            ),
        },
        {
            "doc_id": "PD-12",
            "section": "synthetic_disclaimer",
            "text": (
                "## SYNTHETIC disclaimer\n\n"
                "Harbor Credit, all applicants, loans, payments and bureau rows "
                "in this dataset are fictional. Do not use for real lending, "
                "identity, or compliance decisions. is_synthetic=true on every row."
            ),
        },
    ]
    for c in chunks:
        c["is_synthetic"] = True
        c["lender"] = LENDER_NAME
    return chunks


def make_payment_schedule(
    rng: random.Random,
    *,
    applicant_id: str,
    loan_id: str,
    n_payments: int,
    start: date,
    instalment: float,
    force_late_pattern: list[int] | None = None,
) -> list[dict]:
    """Build payment rows. force_late_pattern is late_days per instalment (len <= n)."""
    rows: list[dict] = []
    for i in range(n_payments):
        due = start + timedelta(days=30 * i)
        if force_late_pattern is not None and i < len(force_late_pattern):
            late_days = int(force_late_pattern[i])
        else:
            # Mostly on-time; occasional mild lateness.
            roll = rng.random()
            if roll < 0.82:
                late_days = 0
            elif roll < 0.93:
                late_days = rng.randint(1, 10)
            else:
                late_days = rng.randint(11, 40)
        paid = due + timedelta(days=late_days)
        rows.append(
            {
                "applicant_id": applicant_id,
                "loan_id": loan_id,
                "due_date": due.isoformat(),
                "paid_date": paid.isoformat(),
                "amount": round(instalment, 2),
                "late_days": late_days,
                "is_synthetic": True,
                "lender": LENDER_NAME,
                "note": "SYNTHETIC",
            }
        )
    return rows


def plant_scenarios(
    rng: random.Random,
    rules: list[dict],
    applications: dict[str, dict],
    bureau_by_applicant: dict[str, dict],
    payments: list[dict],
    names: dict[str, str],
) -> dict:
    """Overwrite planted loans and return truth.json structure."""
    truth: dict = {
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": (
            "SYNTHETIC ground-truth drivers for tests only. "
            "Never provide this file to the agent."
        ),
        "scorecard": {
            "base_score": BASE_SCORE,
            "approve_threshold": APPROVE_THRESHOLD,
            "base_rate_pct": BASE_RATE_PCT,
            "rate_points_per_default": RATE_POINTS_PER_DEFAULT,
            "income_low_threshold": INCOME_LOW_THRESHOLD,
            "amount_to_income_debt_service": AMOUNT_TO_INCOME_DEBT_SERVICE,
        },
        "planted": {},
    }

    # --- LN-1042: decline; late payments + utilisation 91%; income ABOVE threshold ---
    ap_1042 = applicant_id_for(1042)
    names[ap_1042] = synthetic_name(rng)
    prior_loan = "LN-0900"
    # 3 of last 6 over 15 days late (pattern for the prior loan's last 6).
    late_pattern = [0, 18, 22, 5, 30, 10]  # exactly 3 of 6 with late_days > 15
    prior_pays = make_payment_schedule(
        rng,
        applicant_id=ap_1042,
        loan_id=prior_loan,
        n_payments=6,
        start=date(2024, 1, 15),
        instalment=1200.0,
        force_late_pattern=late_pattern,
    )
    payments.extend(prior_pays)
    late_count = sum(1 for p in prior_pays[-6:] if int(p["late_days"]) > 15)
    assert late_count == 3

    bureau_by_applicant[ap_1042] = {
        "applicant_id": ap_1042,
        "score": 670,
        "utilisation": 91,
        "enquiries_6m": 2,
        "defaults": 0,
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    feat_1042 = build_features(
        amount=95_000,
        term=36,
        sector="services",
        years_in_business=6,
        declared_income=72_000,  # ABOVE INCOME_LOW_THRESHOLD
        utilisation=91,
        bureau_score=670,
        enquiries_6m=2,
        defaults=0,
        late_over_15_of_last_6=late_count,
        prior_loans=1,
    )
    dec_1042 = decide(feat_1042, rules)
    assert dec_1042["outcome"] == "decline"
    assert feat_1042["declared_income"] >= INCOME_LOW_THRESHOLD
    assert not any(
        f["rule_id"] == "R01" for f in dec_1042["fired_rules"]
    ), "low-income rule must NOT fire for LN-1042"
    assert any(f["rule_id"] == "R04" for f in dec_1042["fired_rules"])
    assert any(f["rule_id"] == "R02" for f in dec_1042["fired_rules"])

    applications["LN-1042"] = {
        "loan_id": "LN-1042",
        "applicant_id": ap_1042,
        "applicant_name": names[ap_1042],
        "amount": 95_000,
        "term": 36,
        "sector": "services",
        "years_in_business": 6,
        "declared_income": 72_000,
        "outcome": dec_1042["outcome"],
        "rate": dec_1042["rate"],
        "score": dec_1042["score"],
        "reason_code": "UTILISATION_AND_CONDUCT",
        "decided_at": "2025-11-12T10:00:00Z",
        "prior_loans": 1,
        "prior_loan_id": prior_loan,
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    truth["planted"]["LN-1042"] = {
        "outcome": "decline",
        "true_drivers": [
            {
                "feature": "late_over_15_of_last_6",
                "rule_id": "R04",
                "detail": "3 of last 6 payments on prior loan over 15 days late",
            },
            {
                "feature": "utilisation",
                "rule_id": "R02",
                "detail": "utilisation 91 percent",
            },
        ],
        "false_stories": ["low_income", "declared_income_below_threshold"],
        "facts": {
            "utilisation": 91,
            "late_of_last_6_over_15_days": 3,
            "declared_income": 72_000,
            "declared_income_above_policy_threshold": True,
            "income_policy_threshold": INCOME_LOW_THRESHOLD,
            "prior_loan_id": prior_loan,
        },
    }

    # --- LN-1107: repeat borrower; +4 rate vs first loan from 2 bureau defaults ---
    ap_1107 = applicant_id_for(1107)
    names[ap_1107] = synthetic_name(rng)
    first_loan = "LN-0500"
    bureau_by_applicant[ap_1107] = {
        "applicant_id": ap_1107,
        "score": 700,
        "utilisation": 40,
        "enquiries_6m": 1,
        "defaults": 2,  # visible ONLY in bureau
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    # First loan snapshot: same profile but defaults were 0 at that time.
    feat_first = build_features(
        amount=80_000,
        term=24,
        sector="retail",
        years_in_business=4,
        declared_income=88_000,
        utilisation=35,
        bureau_score=700,
        enquiries_6m=1,
        defaults=0,
        late_over_15_of_last_6=0,
        prior_loans=0,
    )
    dec_first = decide(feat_first, rules)
    applications[first_loan] = {
        "loan_id": first_loan,
        "applicant_id": ap_1107,
        "applicant_name": names[ap_1107],
        "amount": 80_000,
        "term": 24,
        "sector": "retail",
        "years_in_business": 4,
        "declared_income": 88_000,
        "outcome": dec_first["outcome"],
        "rate": dec_first["rate"],
        "score": dec_first["score"],
        "reason_code": "APPROVE_STANDARD" if dec_first["outcome"] == "approve" else "DECLINE",
        "decided_at": "2024-06-01T10:00:00Z",
        "prior_loans": 0,
        "prior_loan_id": "",
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    # Prior loan payments (clean).
    payments.extend(
        make_payment_schedule(
            rng,
            applicant_id=ap_1107,
            loan_id=first_loan,
            n_payments=12,
            start=date(2024, 7, 1),
            instalment=900.0,
            force_late_pattern=[0] * 12,
        )
    )
    feat_1107 = build_features(
        amount=80_000,
        term=24,
        sector="retail",
        years_in_business=5,
        declared_income=88_000,
        utilisation=40,
        bureau_score=700,
        enquiries_6m=1,
        defaults=2,
        late_over_15_of_last_6=0,
        prior_loans=1,
    )
    dec_1107 = decide(feat_1107, rules)
    rate_delta = dec_1107["rate"] - dec_first["rate"]
    assert abs(rate_delta - 4.0) < 1e-9, f"expected +4 rate points, got {rate_delta}"

    applications["LN-1107"] = {
        "loan_id": "LN-1107",
        "applicant_id": ap_1107,
        "applicant_name": names[ap_1107],
        "amount": 80_000,
        "term": 24,
        "sector": "retail",
        "years_in_business": 5,
        "declared_income": 88_000,
        "outcome": dec_1107["outcome"],
        "rate": dec_1107["rate"],
        "score": dec_1107["score"],
        "reason_code": "PRICE_FOR_BUREAU_DEFAULTS",
        "decided_at": "2025-09-20T14:30:00Z",
        "prior_loans": 1,
        "prior_loan_id": first_loan,
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    truth["planted"]["LN-1107"] = {
        "outcome": dec_1107["outcome"],
        "true_drivers": [
            {
                "feature": "defaults",
                "rule_id": "R06",
                "detail": "2 bureau defaults visible only via bureau API",
            }
        ],
        "pricing": {
            "first_loan_id": first_loan,
            "first_loan_rate": dec_first["rate"],
            "this_rate": dec_1107["rate"],
            "rate_delta_points": 4.0,
            "defaults": 2,
        },
        "facts": {
            "defaults": 2,
            "defaults_source": "bureau_only",
            "repeat_borrower": True,
        },
    }

    # --- LN-1230: borderline decline; reduce amount 15% flips to approve ---
    # Debt-service R08 (amount_to_income > 2.5) loses points when amount drops.
    ap_1230 = applicant_id_for(1230)
    names[ap_1230] = synthetic_name(rng)
    amount_1230 = 110_000.0
    income_1230 = 42_000.0
    assert amount_1230 / income_1230 > AMOUNT_TO_INCOME_DEBT_SERVICE
    assert (amount_1230 * 0.85) / income_1230 <= AMOUNT_TO_INCOME_DEBT_SERVICE
    bureau_by_applicant[ap_1230] = {
        "applicant_id": ap_1230,
        "score": 640,
        "utilisation": 72,
        "enquiries_6m": 2,
        "defaults": 0,
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    feat_1230 = build_features(
        amount=amount_1230,
        term=36,
        sector="technology",
        years_in_business=3,
        declared_income=income_1230,
        utilisation=72,
        bureau_score=640,
        enquiries_6m=2,
        defaults=0,
        late_over_15_of_last_6=0,
        prior_loans=0,
    )
    dec_1230 = decide(feat_1230, rules)
    assert dec_1230["outcome"] == "decline", dec_1230
    feat_reduced = dict(feat_1230)
    feat_reduced["amount"] = amount_1230 * 0.85
    feat_reduced["amount_to_income"] = (
        feat_reduced["amount"] / feat_reduced["declared_income"]
    )
    dec_reduced = decide(feat_reduced, rules)
    assert dec_reduced["outcome"] == "approve", (
        f"15% amount reduction should flip to approve; got {dec_reduced}"
    )

    applications["LN-1230"] = {
        "loan_id": "LN-1230",
        "applicant_id": ap_1230,
        "applicant_name": names[ap_1230],
        "amount": amount_1230,
        "term": 36,
        "sector": "technology",
        "years_in_business": 3,
        "declared_income": income_1230,
        "outcome": dec_1230["outcome"],
        "rate": dec_1230["rate"],
        "score": dec_1230["score"],
        "reason_code": "DECLINE_BORDERLINE",
        "decided_at": "2025-10-01T09:15:00Z",
        "prior_loans": 0,
        "prior_loan_id": "",
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    truth["planted"]["LN-1230"] = {
        "outcome": "decline",
        "true_drivers": [
            {
                "feature": "amount_to_income",
                "rule_id": "R08",
                "detail": (
                    "borderline decline; reducing amount by 15% drops "
                    "amount-to-income below debt-service band and flips to approve"
                ),
            }
        ],
        "counterfactual": {
            "change": "reduce amount by 15%",
            "documented_flip": (
                "15% amount reduction flips decline to approve (scorecard); "
                "search may find a smaller valid flip"
            ),
            "amount_from": amount_1230,
            "amount_to": amount_1230 * 0.85,
            "flips_to": "approve",
        },
        "facts": {
            "score": dec_1230["score"],
            "approve_threshold": APPROVE_THRESHOLD,
            "amount_to_income": amount_1230 / income_1230,
            "amount_to_income_debt_service": AMOUNT_TO_INCOME_DEBT_SERVICE,
            "declared_income": income_1230,
        },
    }

    # --- LN-1301: recorded reason_code contradicts scorecard evidence ---
    ap_1301 = applicant_id_for(1301)
    names[ap_1301] = synthetic_name(rng)
    bureau_by_applicant[ap_1301] = {
        "applicant_id": ap_1301,
        "score": 560,  # fires R10
        "utilisation": 25,
        "enquiries_6m": 7,  # fires R13
        "defaults": 0,
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    feat_1301 = build_features(
        amount=100_000,
        term=36,
        sector="healthcare",
        years_in_business=7,
        declared_income=110_000,  # strong income — NOT low
        utilisation=25,
        bureau_score=560,
        enquiries_6m=7,
        defaults=0,
        late_over_15_of_last_6=0,
        prior_loans=0,
    )
    dec_1301 = decide(feat_1301, rules)
    assert dec_1301["outcome"] == "decline"
    assert not any(f["rule_id"] == "R01" for f in dec_1301["fired_rules"])
    false_reason = "INCOME_BELOW_THRESHOLD"  # contradicts data
    applications["LN-1301"] = {
        "loan_id": "LN-1301",
        "applicant_id": ap_1301,
        "applicant_name": names[ap_1301],
        "amount": 100_000,
        "term": 36,
        "sector": "healthcare",
        "years_in_business": 7,
        "declared_income": 110_000,
        "outcome": dec_1301["outcome"],
        "rate": dec_1301["rate"],
        "score": dec_1301["score"],
        "reason_code": false_reason,
        "decided_at": "2025-08-18T16:45:00Z",
        "prior_loans": 0,
        "prior_loan_id": "",
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    truth["planted"]["LN-1301"] = {
        "outcome": "decline",
        "true_drivers": [
            {
                "feature": "bureau_score",
                "rule_id": "R10",
                "detail": "bureau score below floor",
            },
            {
                "feature": "enquiries_6m",
                "rule_id": "R13",
                "detail": "six or more enquiries in six months",
            },
        ],
        "recorded_reason_code": false_reason,
        "recorded_reason_supported": False,
        "agent_should_flag": "recorded reason not supported",
        "facts": {
            "declared_income": 110_000,
            "declared_income_above_policy_threshold": True,
            "bureau_score": 560,
            "enquiries_6m": 7,
        },
    }

    return truth


def random_application(
    rng: random.Random,
    *,
    loan_id: str,
    applicant_id: str,
    name: str,
    bureau: dict,
    rules: list[dict],
    prior_loans: int = 0,
) -> tuple[dict, int]:
    """Create one random application consistent with the scorecard."""
    amount = float(rng.choice([40_000, 55_000, 70_000, 85_000, 100_000, 120_000, 150_000, 200_000, 280_000]))
    term = int(rng.choice([12, 24, 36, 48, 60]))
    sector = rng.choice(SECTORS)
    years = round(rng.uniform(0.5, 15.0), 1)
    income = float(rng.randint(25_000, 180_000))
    late = int(rng.choices([0, 1, 2, 3, 4], weights=[55, 20, 12, 8, 5])[0])
    feat = build_features(
        amount=amount,
        term=term,
        sector=sector,
        years_in_business=years,
        declared_income=income,
        utilisation=float(bureau["utilisation"]),
        bureau_score=float(bureau["score"]),
        enquiries_6m=int(bureau["enquiries_6m"]),
        defaults=int(bureau["defaults"]),
        late_over_15_of_last_6=late,
        prior_loans=prior_loans,
    )
    dec = decide(feat, rules)
    year = rng.randint(2023, 2025)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    row = {
        "loan_id": loan_id,
        "applicant_id": applicant_id,
        "applicant_name": name,
        "amount": amount,
        "term": term,
        "sector": sector,
        "years_in_business": years,
        "declared_income": income,
        "outcome": dec["outcome"],
        "rate": dec["rate"],
        "score": dec["score"],
        "reason_code": (
            "APPROVE_STANDARD" if dec["outcome"] == "approve" else "SCORECARD_DECLINE"
        ),
        "decided_at": f"{year:04d}-{month:02d}-{day:02d}T12:00:00Z",
        "prior_loans": prior_loans,
        "prior_loan_id": "",
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC",
    }
    return row, late


def generate(seed: int = SEED) -> dict:
    rng = random.Random(seed)
    rules = policy_rules()
    assert 20 <= len(rules) <= 30

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Applicants / bureau pool (1400). Some applicants reused for repeats.
    names: dict[str, str] = {}
    bureau_by_applicant: dict[str, dict] = {}
    for i in range(1, N_BUREAU + 1):
        ap = applicant_id_for(i)
        names[ap] = synthetic_name(rng)
        bureau_by_applicant[ap] = {
            "applicant_id": ap,
            "score": int(rng.randint(520, 820)),
            "utilisation": int(rng.randint(5, 95)),
            "enquiries_6m": int(rng.randint(0, 8)),
            "defaults": int(rng.choices([0, 1, 2, 3], weights=[70, 18, 9, 3])[0]),
            "is_synthetic": True,
            "lender": LENDER_NAME,
            "note": "SYNTHETIC",
        }

    applications: dict[str, dict] = {}
    payments: list[dict] = []
    late_by_loan: dict[str, int] = {}

    # Fill LN-0001 .. LN-2000 with random rows first (planted overwrite later).
    for i in range(1, N_APPLICATIONS + 1):
        lid = loan_id_for(i)
        # Map application index onto bureau pool (with wrap for extras).
        ap_idx = ((i - 1) % N_BUREAU) + 1
        ap = applicant_id_for(ap_idx)
        # Occasional repeat flag for variety.
        prior = 1 if i % 17 == 0 else 0
        row, late = random_application(
            rng,
            loan_id=lid,
            applicant_id=ap,
            name=names[ap],
            bureau=bureau_by_applicant[ap],
            rules=rules,
            prior_loans=prior,
        )
        applications[lid] = row
        late_by_loan[lid] = late

    truth = plant_scenarios(
        rng, rules, applications, bureau_by_applicant, payments, names
    )

    # Payments for non-planted prior conduct: distribute toward ~30k rows.
    # Planted scenarios already added some rows.
    remaining = N_PAYMENTS_TARGET - len(payments)
    # Attach payment histories to a subset of loans (approved-looking or random).
    loan_ids = sorted(applications.keys())
    rng_pay = random.Random(seed + 7)
    per_loan = max(6, remaining // max(1, len(loan_ids) // 2))
    for lid in loan_ids:
        if len(payments) >= N_PAYMENTS_TARGET:
            break
        app = applications[lid]
        # Skip if this loan already has payments (planted priors / first loans).
        if any(p["loan_id"] == lid for p in payments):
            continue
        n = min(per_loan, N_PAYMENTS_TARGET - len(payments))
        if n <= 0:
            break
        late_n = late_by_loan.get(lid, 0)
        # Build a pattern whose last-6 over-15 count matches late_n when possible.
        pattern = [0] * n
        if late_n > 0 and n >= 6:
            positions = list(range(n - 6, n))
            rng_pay.shuffle(positions)
            for pos in positions[:late_n]:
                pattern[pos] = rng_pay.randint(16, 35)
        start_year = int(app["decided_at"][:4]) - 1
        payments.extend(
            make_payment_schedule(
                rng_pay,
                applicant_id=app["applicant_id"],
                loan_id=lid,
                n_payments=n,
                start=date(start_year, 1, 10),
                instalment=round(float(app["amount"]) / max(int(app["term"]), 1), 2),
                force_late_pattern=pattern,
            )
        )

    # If still short, pad with extra instalments on early loans.
    pad_i = 0
    while len(payments) < N_PAYMENTS_TARGET:
        lid = loan_ids[pad_i % len(loan_ids)]
        app = applications[lid]
        payments.append(
            {
                "applicant_id": app["applicant_id"],
                "loan_id": lid,
                "due_date": date(2022, 1, 1).isoformat(),
                "paid_date": date(2022, 1, 1).isoformat(),
                "amount": 100.0,
                "late_days": 0,
                "is_synthetic": True,
                "lender": LENDER_NAME,
                "note": "SYNTHETIC",
            }
        )
        pad_i += 1

    # Trim if we overshot slightly.
    payments = payments[: max(N_PAYMENTS_TARGET, len(payments))]
    # Keep at least the target; allow a small surplus from planting.
    if len(payments) > N_PAYMENTS_TARGET + 500:
        payments = payments[: N_PAYMENTS_TARGET + 500]

    # Ensure bureau still has exactly 1400 rows (planted may have reused ids).
    bureau_rows = [bureau_by_applicant[applicant_id_for(i)] for i in range(1, N_BUREAU + 1)]
    assert len(bureau_rows) == N_BUREAU

    # Write outputs.
    app_fields = [
        "loan_id",
        "applicant_id",
        "applicant_name",
        "amount",
        "term",
        "sector",
        "years_in_business",
        "declared_income",
        "outcome",
        "rate",
        "score",
        "reason_code",
        "decided_at",
        "prior_loans",
        "prior_loan_id",
        "is_synthetic",
        "lender",
        "note",
    ]
    app_rows = [applications[loan_id_for(i)] for i in range(1, N_APPLICATIONS + 1)]
    # Ensure planted overlays are present (LN-0500 is inside 1..2000; LN-0900 may not be).
    # LN-0900 is a prior loan id for payments only — not required in applications.
    write_csv(OUT_DIR / "applications.csv", app_rows, app_fields)

    pay_fmt = write_payments(
        OUT_DIR / "payments.parquet",
        OUT_DIR / "payments.csv",
        payments,
    )

    bureau_payload = {
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "note": "SYNTHETIC bureau extract for local mock REST service on :8077",
        "rows": bureau_rows,
    }
    (OUT_DIR / "bureau.json").write_text(
        json.dumps(bureau_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    rule_fields = [
        "rule_id",
        "feature",
        "operator",
        "threshold",
        "points",
        "text",
        "is_synthetic",
        "lender",
    ]
    write_csv(OUT_DIR / "policy_rules.csv", rules, rule_fields)

    chunks = policy_doc_chunks()
    # Write a single markdown file plus a JSONL index of chunks.
    md_parts = [c["text"] for c in chunks]
    (OUT_DIR / "policy_docs.md").write_text(
        "\n\n---\n\n".join(md_parts) + "\n",
        encoding="utf-8",
    )
    chunks_path = OUT_DIR / "policy_docs_chunks.json"
    chunks_path.write_text(
        json.dumps(chunks, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    TRUTH_PATH.write_text(
        json.dumps(truth, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    meta = {
        "is_synthetic": True,
        "lender": LENDER_NAME,
        "seed": seed,
        "applications": len(app_rows),
        "payments": len(payments),
        "payments_format": pay_fmt,
        "bureau": len(bureau_rows),
        "policy_rules": len(rules),
        "policy_doc_chunks": len(chunks),
    }
    (OUT_DIR / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Generated SYNTHETIC Harbor Credit dataset: "
        f"{meta['applications']} applications, {meta['payments']} payments "
        f"({pay_fmt}), {meta['bureau']} bureau, {meta['policy_rules']} rules."
    )
    return meta


def dataset_checksum(out_dir: Path = OUT_DIR) -> str:
    """Stable checksum of generated artefacts (for determinism tests)."""
    h = hashlib.sha256()
    for name in (
        "applications.csv",
        "payments.csv",
        "payments.parquet",
        "bureau.json",
        "policy_rules.csv",
        "policy_docs.md",
        "policy_docs_chunks.json",
        "meta.json",
    ):
        path = out_dir / name
        if not path.exists():
            continue
        h.update(name.encode())
        h.update(path.read_bytes())
    # truth.json lives beside out/
    if TRUTH_PATH.exists():
        h.update(b"truth.json")
        h.update(TRUTH_PATH.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    generate(SEED)
    print(f"checksum={dataset_checksum()}")
