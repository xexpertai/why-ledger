"""Fixed read-only evidence plan for live ``LIVE_AGENT_MODE=planned`` runs.

Code gathers application, bureau, payment conduct, scorecard drivers, policy
lookup, and peer cases via :class:`~whyledger.zetaris.ZetarisClient`. Each step
gets a stable ``evidence_id`` for the draft-claims LLM prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

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
class PlannedEvidenceItem:
    """One planned query result bound to an ``evidence_id``."""

    evidence_id: str
    label: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    query_id: str


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


def _policy_lookup_sql(
    rule_id: str | None, search: str | None, client: ZetarisClient
) -> str:
    if rule_id:
        rid = rule_id.replace("'", "''")
        rules = table_ref("policy_rules", client)
        return (
            "SELECT rule_id, feature, operator, threshold, points, text "
            f"FROM {rules} WHERE rule_id = '{rid}'"
        )
    term = (search or "").replace("'", "''")
    docs = table_ref("policy_docs", client)
    return (
        "SELECT doc_id, section, substr(text, 1, 200) AS excerpt "
        f"FROM {docs} WHERE text LIKE '%{term}%' OR section LIKE '%{term}%' "
        "LIMIT 5"
    )


def _peer_cases_sql(loan_id: str, client: ZetarisClient) -> str:
    lid = loan_id.replace("'", "''")
    apps = table_ref("applications", client)
    return (
        "SELECT a2.loan_id, a2.outcome, a2.score AS scorecard_points, a2.amount "
        f"FROM {apps} a1 "
        f"JOIN {apps} a2 ON a1.outcome = a2.outcome AND a2.loan_id != a1.loan_id "
        f"WHERE a1.loan_id = '{lid}' "
        "ORDER BY ABS(CAST(a2.score AS DOUBLE) - CAST(a1.score AS DOUBLE)) "
        "LIMIT 5"
    )


def _run(client: ZetarisClient, sql: str) -> QueryResult:
    return client.run_sql(sql)


def gather_planned_evidence(
    client: ZetarisClient, loan_id: str
) -> list[PlannedEvidenceItem]:
    """Execute the fixed evidence plan; caller logs rows into the ledger."""
    lid = loan_id.replace("'", "''")
    apps = table_ref("applications", client)
    app_sql = (
        "SELECT loan_id, outcome, reason_code, amount, term, sector, "
        "years_in_business, declared_income, prior_loans, prior_loan_id, "
        f"applicant_id, score AS scorecard_points FROM {apps} WHERE loan_id = '{lid}'"
    )
    app_res = _run(client, app_sql)
    if not app_res.rows:
        raise ValueError(f"no application for loan_id={loan_id}")
    app_row = app_res.rows[0]
    applicant_id = str(app_row["applicant_id"]).replace("'", "''")

    bureau_t = table_ref("bureau", client)
    bureau_sql = (
        "SELECT utilisation, score AS bureau_score, enquiries_6m, defaults "
        f"FROM {bureau_t} WHERE applicant_id = '{applicant_id}'"
    )
    bureau_res = _run(client, bureau_sql)

    prior = str(app_row.get("prior_loan_id") or "")
    pay_sql = _late_count_sql(prior, client) if prior else (
        f"SELECT 0 AS late_count FROM {apps} WHERE loan_id = '{lid}' LIMIT 1"
    )
    pay_res = _run(client, pay_sql)

    rules_t = table_ref("policy_rules", client)
    all_rules_sql = (
        "SELECT rule_id, feature, operator, threshold, points, text "
        f"FROM {rules_t} ORDER BY rule_id"
    )
    rules_res = _run(client, all_rules_sql)
    rules: list[dict[str, Any]] = []
    for row in rules_res.rows:
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
    amount = float(app_row["amount"])
    income = float(app_row["declared_income"])
    sector = str(app_row.get("sector") or "")
    late = 0.0
    if pay_res.rows:
        late = float(pay_res.rows[0].get("late_count", 0))
    b = bureau_res.rows[0] if bureau_res.rows else {}
    features = {
        "amount": amount,
        "term": float(app_row["term"]),
        "years_in_business": float(app_row["years_in_business"]),
        "declared_income": income,
        "utilisation": float(b.get("utilisation", 0)),
        "bureau_score": float(b.get("bureau_score", 0)),
        "enquiries_6m": float(b.get("enquiries_6m", 0)),
        "defaults": float(b.get("defaults", 0)),
        "late_over_15_of_last_6": late,
        "amount_to_income": amount / income if income else 0.0,
        "prior_loans": float(app_row.get("prior_loans") or 0),
        "sector_risk": _sector_risk(sector),
    }
    sc = score(features, rules)
    ranked = drivers_by_points(sc)
    fired_ids = [h.rule_id for h in ranked]
    if fired_ids:
        in_list = ", ".join(
            f"'{rid.replace(chr(39), chr(39) + chr(39))}'" for rid in fired_ids
        )
        driver_sql = (
            "SELECT rule_id, feature, points, text "
            f"FROM {rules_t} WHERE rule_id IN ({in_list})"
        )
    else:
        driver_sql = (
            "SELECT rule_id, feature, points, text "
            f"FROM {rules_t} WHERE 1=0"
        )
    driver_res = _run(client, driver_sql)

    reason = str(app_row.get("reason_code") or "conduct")
    policy_term = reason.replace("_", " ").lower()
    policy_sql = _policy_lookup_sql(None, policy_term, client)
    policy_res = _run(client, policy_sql)

    peer_sql = _peer_cases_sql(loan_id, client)
    peer_res = _run(client, peer_sql)

    items: list[PlannedEvidenceItem] = []
    for evidence_id, label, result in (
        ("ev_application", "application", app_res),
        ("ev_bureau", "bureau", bureau_res),
        ("ev_payment_conduct", "payment_conduct", pay_res),
        ("ev_scorecard_drivers", "scorecard_drivers", driver_res),
        ("ev_policy_lookup", "policy_lookup", policy_res),
        ("ev_peer_cases", "peer_cases", peer_res),
    ):
        items.append(
            PlannedEvidenceItem(
                evidence_id=evidence_id,
                label=label,
                sql=result.sql,
                columns=list(result.columns),
                rows=list(result.rows),
                query_id=result.query_id,
            )
        )
    return items


def evidence_table_json(items: list[PlannedEvidenceItem]) -> str:
    """Compact JSON table for one LLM user message."""
    payload = [
        {
            "evidence_id": it.evidence_id,
            "label": it.label,
            "sql": it.sql,
            "columns": it.columns,
            "rows": it.rows,
        }
        for it in items
    ]
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
