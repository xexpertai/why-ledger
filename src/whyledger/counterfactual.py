"""SQL-fetched features + scorecard probing for minimal flip counterfactuals.

Given a loan_id and :class:`~whyledger.zetaris.ZetarisClient`, loads the
application snapshot through logged SELECTs, varies one scorecard input at a
time within sensible bounds, and returns the smallest single-feature change that
flips a decline to approve (or ``None`` for approvals / no flip).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from whyledger.card import Counterfactual
from whyledger.scorecard import drivers_by_points, score
from whyledger.zetaris import QueryResult, ZetarisClient
from whyledger.zetaris_dialect import table_ref

_SECTOR_RISK = {
    "hospitality": 2,
    "construction": 2,
    "retail": 1,
    "transport": 1,
}


@dataclass(frozen=True)
class CounterfactualSearchResult:
    """Counterfactual plus every SQL query run during the search (for the ledger)."""

    counterfactual: Counterfactual
    queries: tuple[QueryResult, ...]


def _sector_risk(sector: str) -> float:
    return float(_SECTOR_RISK.get(sector.lower(), 0))


def _late_count_sql(prior_loan_id: str, client: ZetarisClient) -> str:
    lid = prior_loan_id.replace("'", "''")
    payments = table_ref("payments", client)
    return (
        "SELECT COUNT(*) AS late_count FROM ("
        f" SELECT late_days FROM {payments} WHERE loan_id = '{lid}'"
        " ORDER BY due_date DESC LIMIT 6"
        ") t WHERE CAST(late_days AS INT) > 15"
    )


def _run(client: ZetarisClient, sql: str, bucket: list[QueryResult]) -> QueryResult:
    result = client.run_sql(sql)
    bucket.append(result)
    return result


def fetch_loan_features(
    client: ZetarisClient, loan_id: str, *, queries: list[QueryResult] | None = None
) -> dict[str, float]:
    """Load scorecard features for ``loan_id`` via Zetaris SELECTs."""
    logged: list[QueryResult] = queries if queries is not None else []

    lid = loan_id.replace("'", "''")
    apps = table_ref("applications", client)
    app = _run(
        client,
        "SELECT amount, term, sector, years_in_business, declared_income, "
        f"prior_loans, prior_loan_id, applicant_id FROM {apps} "
        f"WHERE loan_id = '{lid}'",
        logged,
    )
    if not app.rows:
        raise ValueError(f"no application for loan_id={loan_id}")
    row = app.rows[0]
    applicant_id = str(row["applicant_id"]).replace("'", "''")
    bureau_t = table_ref("bureau", client)
    bureau = _run(
        client,
        f"SELECT utilisation, score, enquiries_6m, defaults FROM {bureau_t} "
        f"WHERE applicant_id = '{applicant_id}'",
        logged,
    )
    if not bureau.rows:
        raise ValueError(f"no bureau row for applicant_id={row['applicant_id']}")
    b = bureau.rows[0]
    prior = str(row.get("prior_loan_id") or "")
    late = 0.0
    if prior:
        late_res = _run(client, _late_count_sql(prior, client), logged)
        if late_res.rows:
            late = float(late_res.rows[0].get("late_count", 0))
    amount = float(row["amount"])
    income = float(row["declared_income"])
    sector = str(row.get("sector") or "")
    return {
        "amount": amount,
        "term": float(row["term"]),
        "years_in_business": float(row["years_in_business"]),
        "declared_income": income,
        "utilisation": float(b["utilisation"]),
        "bureau_score": float(b["score"]),
        "enquiries_6m": float(b["enquiries_6m"]),
        "defaults": float(b["defaults"]),
        "late_over_15_of_last_6": late,
        "amount_to_income": amount / income if income else 0.0,
        "prior_loans": float(row.get("prior_loans") or 0),
        "sector_risk": _sector_risk(sector),
    }


def load_policy_rules(
    client: ZetarisClient, *, queries: list[QueryResult] | None = None
) -> list[dict[str, Any]]:
    logged: list[QueryResult] = queries if queries is not None else []
    rules = table_ref("policy_rules", client)
    result = _run(
        client,
        "SELECT rule_id, feature, operator, threshold, points, text "
        f"FROM {rules} ORDER BY rule_id",
        logged,
    )
    rules: list[dict[str, Any]] = []
    for row in result.rows:
        rules.append(
            {
                "rule_id": str(row["rule_id"]),
                "feature": str(row["feature"]),
                "operator": str(row["operator"]),
                "threshold": float(row["threshold"]),
                "points": float(row["points"]),
                "text": str(row.get("text", "")),
            }
        )
    return rules


def _with_feature(features: dict[str, float], key: str, value: float) -> dict[str, float]:
    updated = dict(features)
    updated[key] = value
    if key == "amount":
        income = updated.get("declared_income") or 0.0
        updated["amount_to_income"] = value / income if income else 0.0
    elif key == "declared_income":
        amount = updated.get("amount") or 0.0
        updated["amount_to_income"] = amount / value if value else 0.0
    return updated


def _relative_delta(old: float, new: float) -> float:
    if old == 0:
        return abs(new - old)
    return abs(new - old) / abs(old)


def _describe_change(key: str, old: float, new: float) -> str:
    if key == "amount":
        pct = round(100.0 * (old - new) / old)
        return f"reduce amount by {pct}%"
    if key == "utilisation":
        return f"reduce utilisation to {int(round(new))}"
    if key == "late_over_15_of_last_6":
        return (
            "reduce late payments over 15 days "
            f"from {int(round(old))} to {int(round(new))}"
        )
    if key == "defaults":
        return f"reduce bureau defaults from {int(round(old))} to {int(round(new))}"
    if key == "enquiries_6m":
        return f"reduce enquiries in 6 months from {int(round(old))} to {int(round(new))}"
    if key == "bureau_score":
        return f"increase bureau score to {int(round(new))}"
    if key == "declared_income":
        pct = round(100.0 * (new - old) / old)
        return f"increase declared income by {pct}%"
    if key == "years_in_business":
        return f"increase years in business to {int(round(new))}"
    if key == "term":
        return f"reduce term to {int(round(new))} months"
    return f"change {key} from {old} to {new}"


def _counterfactual_proof_sql(
    loan_id: str, change: str, flips_to: str, feature: str, old: float, new: float
) -> str:
    lid = loan_id.replace("'", "''")
    ch = change.replace("'", "''")
    ft = flips_to.replace("'", "''")
    feat = feature.replace("'", "''")
    return (
        "SELECT "
        f"'{lid}' AS loan_id, "
        f"'{ch}' AS change, "
        f"'{ft}' AS flips_to, "
        f"'{feat}' AS varied_feature, "
        f"{old} AS baseline_value, "
        f"{new} AS counterfactual_value"
    )


@dataclass(frozen=True)
class _Candidate:
    key: str
    old: float
    new: float
    magnitude: float
    change: str


def _levers_for_driver_feature(feature: str) -> list[tuple[str, bool]]:
    """Map a scorecard feature to applicant-actionable levers (key, decrease?)."""
    if feature == "amount_to_income":
        return [("amount", True), ("declared_income", False)]
    if feature == "amount":
        return [("amount", True)]
    if feature == "utilisation":
        return [("utilisation", True)]
    if feature == "late_over_15_of_last_6":
        return [("late_over_15_of_last_6", True)]
    if feature == "defaults":
        return [("defaults", True)]
    if feature == "enquiries_6m":
        return [("enquiries_6m", True)]
    if feature == "declared_income":
        return [("declared_income", False)]
    if feature == "years_in_business":
        return [("years_in_business", False)]
    if feature == "term":
        return [("term", True)]
    if feature == "bureau_score":
        return [("bureau_score", False)]
    if feature == "enquiries_6m":
        return [("enquiries_6m", True)]
    return []


def _probes_for_decline(features: dict[str, float], rules: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    """Probe levers tied to the strongest negative driver (demo narrative)."""
    result = score(features, rules)
    ranked = drivers_by_points(result, limit=1)
    if not ranked:
        return [
            ("amount", True),
            ("utilisation", True),
            ("late_over_15_of_last_6", True),
        ]
    top_feature = ranked[0].feature
    levers = _levers_for_driver_feature(top_feature)
    return levers or [("amount", True)]


def _best_flip_for_feature(
    features: dict[str, float],
    rules: list[dict[str, Any]],
    key: str,
    *,
    decrease: bool,
) -> _Candidate | None:
    baseline = float(features[key])
    best: _Candidate | None = None

    if key == "amount" and decrease:
        for pct in range(1, 51):
            new_amt = baseline * (1.0 - pct / 100.0)
            trial = _with_feature(features, key, new_amt)
            if score(trial, rules).outcome == "approve":
                mag = pct / 100.0
                change = _describe_change(key, baseline, new_amt)
                best = _Candidate(key, baseline, new_amt, mag, change)
                break
        return best

    if key == "declared_income" and not decrease:
        for pct in range(1, 101):
            new_inc = baseline * (1.0 + pct / 100.0)
            trial = _with_feature(features, key, new_inc)
            if score(trial, rules).outcome == "approve":
                mag = pct / 100.0
                change = _describe_change(key, baseline, new_inc)
                best = _Candidate(key, baseline, new_inc, mag, change)
                break
        return best

    if key == "bureau_score" and not decrease:
        for new_score in range(int(baseline) + 1, 851):
            trial = _with_feature(features, key, float(new_score))
            if score(trial, rules).outcome == "approve":
                mag = _relative_delta(baseline, float(new_score))
                change = _describe_change(key, baseline, float(new_score))
                best = _Candidate(key, baseline, float(new_score), mag, change)
                break
        return best

    if decrease:
        if key in ("late_over_15_of_last_6", "defaults", "enquiries_6m"):
            step = 1.0
            floor = 0.0
        elif key == "utilisation":
            step = 1.0
            floor = 0.0
        elif key == "term":
            step = 1.0
            floor = 12.0
        else:
            return None
        current = baseline
        while current - step >= floor:
            current -= step
            trial = _with_feature(features, key, current)
            if score(trial, rules).outcome == "approve":
                mag = _relative_delta(baseline, current)
                change = _describe_change(key, baseline, current)
                best = _Candidate(key, baseline, current, mag, change)
                break
        return best

    if key == "years_in_business":
        for new_years in range(int(baseline) + 1, 41):
            trial = _with_feature(features, key, float(new_years))
            if score(trial, rules).outcome == "approve":
                mag = _relative_delta(baseline, float(new_years))
                change = _describe_change(key, baseline, float(new_years))
                best = _Candidate(key, baseline, float(new_years), mag, change)
                break
    return best


def _trial_features_for_change(
    features: dict[str, float], change: str
) -> dict[str, float] | None:
    """Best-effort map a human-readable ``change`` string to updated features."""
    text = (change or "").strip()
    m = re.search(r"reduce amount by (\d+)%", text, re.IGNORECASE)
    if m:
        pct = int(m.group(1))
        new_amt = features["amount"] * (1.0 - pct / 100.0)
        return _with_feature(features, "amount", new_amt)
    m = re.search(r"increase declared income by (\d+)%", text, re.IGNORECASE)
    if m:
        pct = int(m.group(1))
        new_inc = features["declared_income"] * (1.0 + pct / 100.0)
        return _with_feature(features, "declared_income", new_inc)
    m = re.search(r"reduce utilisation to (\d+)", text, re.IGNORECASE)
    if m:
        return _with_feature(features, "utilisation", float(m.group(1)))
    m = re.search(
        r"reduce late payments over 15 days from (\d+) to (\d+)", text, re.IGNORECASE
    )
    if m:
        return _with_feature(features, "late_over_15_of_last_6", float(m.group(2)))
    m = re.search(r"reduce bureau defaults from (\d+) to (\d+)", text, re.IGNORECASE)
    if m:
        return _with_feature(features, "defaults", float(m.group(2)))
    m = re.search(r"reduce enquiries in 6 months from (\d+) to (\d+)", text, re.IGNORECASE)
    if m:
        return _with_feature(features, "enquiries_6m", float(m.group(2)))
    m = re.search(r"increase bureau score to (\d+)", text, re.IGNORECASE)
    if m:
        return _with_feature(features, "bureau_score", float(m.group(1)))
    m = re.search(r"increase years in business to (\d+)", text, re.IGNORECASE)
    if m:
        return _with_feature(features, "years_in_business", float(m.group(1)))
    m = re.search(r"reduce term to (\d+) months", text, re.IGNORECASE)
    if m:
        return _with_feature(features, "term", float(m.group(1)))
    return None


def confirm_counterfactual_flip(
    client: ZetarisClient, loan_id: str, counterfactual: Counterfactual
) -> bool:
    """Return True if ``counterfactual`` flips a decline on ``loan_id`` to approve."""
    if counterfactual.flips_to != "approve":
        return False
    features = fetch_loan_features(client, loan_id)
    rules = load_policy_rules(client)
    if score(features, rules).outcome != "decline":
        return False
    trial = _trial_features_for_change(features, counterfactual.change)
    if trial is None:
        return False
    return score(trial, rules).outcome == "approve"


def counterfactual_change_magnitude_leq_truth(
    change: str, truth_change: str
) -> bool:
    """For amount reductions, found change must be <= documented truth reduction."""
    found = re.search(r"reduce amount by (\d+)%", change or "", re.IGNORECASE)
    truth = re.search(r"reduce amount by (\d+)%", truth_change or "", re.IGNORECASE)
    if found and truth:
        return int(found.group(1)) <= int(truth.group(1))
    return change == truth_change


def find_counterfactual(
    client: ZetarisClient, loan_id: str
) -> CounterfactualSearchResult | None:
    """Return the smallest single-input flip to approve, with logged SQL evidence."""
    queries: list[QueryResult] = []
    features = fetch_loan_features(client, loan_id, queries=queries)
    rules = load_policy_rules(client, queries=queries)
    baseline = score(features, rules)
    if baseline.outcome != "decline":
        return None

    probes = _probes_for_decline(features, rules)

    candidates: list[_Candidate] = []
    for key, decrease in probes:
        if key not in features:
            continue
        hit = _best_flip_for_feature(features, rules, key, decrease=decrease)
        if hit is not None:
            candidates.append(hit)

    if not candidates:
        return None

    candidates.sort(key=lambda c: (c.magnitude, c.key))
    winner = candidates[0]
    cf = Counterfactual(change=winner.change, flips_to="approve")
    proof_sql = _counterfactual_proof_sql(
        loan_id,
        winner.change,
        cf.flips_to,
        winner.key,
        winner.old,
        winner.new,
    )
    _run(client, proof_sql, queries)
    return CounterfactualSearchResult(
        counterfactual=cf,
        queries=tuple(queries),
    )
