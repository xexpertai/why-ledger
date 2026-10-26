"""Deterministic check: recorded reason_code vs scorecard-triggered rules (SQL-grounded)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from whyledger.ledger import Ledger, rows_sha256
from whyledger.scorecard import evaluate_rule, score
from whyledger.zetaris import QueryResult, ZetarisClient
from whyledger.zetaris_dialect import table_ref

ReasonCheckStatus = Literal["UNSUPPORTED", "CONSISTENT", "NOT_CHECKED"]


def format_whole_number(value: float | int | None) -> str:
    """Thousands separators, no trailing .0 (for income/threshold in UI copy)."""
    if value is None:
        return "?"
    return f"{int(round(float(value))):,}"


class ReasonCheckEvidence(BaseModel):
    """One logged query backing the reason check."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    sql: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    sha256: str


class ReasonCheck(BaseModel):
    """Outcome of comparing the decision record reason_code to live data."""

    model_config = ConfigDict(extra="forbid")

    status: ReasonCheckStatus
    headline: str
    recorded_reason_code: str
    detail: str
    evidence: list[ReasonCheckEvidence] = Field(default_factory=list)


# Maps operations reason_code to scorecard rule ids used for consistency checks.
_REASON_RULE_IDS: dict[str, tuple[str, ...]] = {
    "INCOME_BELOW_THRESHOLD": ("R01",),
    "UTILISATION_AND_CONDUCT": ("R02", "R04"),
    "PRICE_FOR_BUREAU_DEFAULTS": ("R06",),
    "DECLINE_BORDERLINE": ("R08", "R19", "R20"),
    "APPROVE_STANDARD": (),
}


def _append_query(
    client: ZetarisClient,
    ledger: Ledger | None,
    sql: str,
    bucket: list[ReasonCheckEvidence],
) -> QueryResult:
    result = client.run_sql(sql)
    sha = rows_sha256(result.rows)
    qid = result.query_id
    if ledger is not None:
        ledger.append_evidence(
            query_id=qid,
            sql=result.sql,
            columns=result.columns,
            rows=result.rows,
            sha256=sha,
        )
    bucket.append(
        ReasonCheckEvidence(
            query_id=qid,
            sql=result.sql,
            rows=result.rows,
            sha256=sha,
        )
    )
    return result


def _rule_by_id(rules: list[dict[str, Any]], rule_id: str) -> dict[str, Any] | None:
    for rule in rules:
        if str(rule.get("rule_id")) == rule_id:
            return rule
    return None


def _income_threshold_sql(client: ZetarisClient) -> str:
    rules = table_ref("policy_rules", client)
    return (
        "SELECT rule_id, feature, operator, threshold, points "
        f"FROM {rules} WHERE rule_id = 'R01'"
    )


def format_reason_detail(recorded_code: str, data_shows: str) -> str:
    """One sentence: record vs data (data_shows is only the Y clause, no prefix)."""
    code = (recorded_code or "").strip()
    tail = (data_shows or "").strip()
    if tail.lower().startswith("the data shows "):
        tail = tail[len("the data shows ") :].strip()
    return f"The record says {code}; the data shows {tail}"


def _reason_code_sql(client: ZetarisClient, loan_id: str) -> str:
    lid = loan_id.replace("'", "''")
    apps = table_ref("applications", client)
    return (
        f"SELECT reason_code, declared_income, outcome FROM {apps} "
        f"WHERE loan_id = '{lid}'"
    )


def _evaluate_reason_consistency(
    reason_code: str,
    features: dict[str, float],
    rules: list[dict[str, Any]],
    outcome: str,
) -> tuple[ReasonCheckStatus, str]:
    """Return (status, detail sentence: the record says X; the data shows Y)."""
    code = (reason_code or "").strip()
    if not code:
        return "NOT_CHECKED", "No recorded reason_code on the application row."

    if code == "APPROVE_STANDARD":
        if outcome == "approve":
            return (
                "CONSISTENT",
                format_reason_detail(code, "outcome approve"),
            )
        return (
            "UNSUPPORTED",
            format_reason_detail(code, f"outcome {outcome}"),
        )

    rule_ids = _REASON_RULE_IDS.get(code)
    if rule_ids is None:
        return "NOT_CHECKED", f"The record says reason_code {code}; no automated rule mapping."

    if code == "UTILISATION_AND_CONDUCT":
        util_hit = evaluate_rule(_rule_by_id(rules, "R02") or {}, features)
        late_hit = evaluate_rule(_rule_by_id(rules, "R04") or {}, features)
        if util_hit.fired or late_hit.fired:
            parts = []
            if util_hit.fired:
                parts.append(f"utilisation {util_hit.feature_value} (rule R02)")
            if late_hit.fired:
                parts.append(
                    f"late payments over 15 days count {late_hit.feature_value} (rule R04)"
                )
            return (
                "CONSISTENT",
                format_reason_detail(code, " and ".join(parts) + "."),
            )
        return (
            "UNSUPPORTED",
            format_reason_detail(
                code,
                "neither high utilisation nor late-payment conduct rules fired.",
            ),
        )

    if code == "PRICE_FOR_BUREAU_DEFAULTS":
        hit = evaluate_rule(_rule_by_id(rules, "R06") or {}, features)
        if hit.fired:
            return (
                "CONSISTENT",
                format_reason_detail(
                    code, f"bureau defaults {hit.feature_value} (rule R06)."
                ),
            )
        return (
            "UNSUPPORTED",
            format_reason_detail(
                code,
                f"bureau defaults {features.get('defaults')} (rule R06 not triggered).",
            ),
        )

    if code == "DECLINE_BORDERLINE":
        fired = [
            evaluate_rule(_rule_by_id(rules, rid) or {}, features)
            for rid in rule_ids
        ]
        any_fired = [h for h in fired if h.fired]
        if any_fired:
            top = any_fired[0]
            return (
                "CONSISTENT",
                format_reason_detail(
                    code,
                    f"scorecard rule {top.rule_id} fired "
                    f"({top.feature} {top.feature_value}).",
                ),
            )
        return (
            "CONSISTENT",
            format_reason_detail(
                code, f"decline with score {score(features, rules).score}."
            ),
        )

    if code == "INCOME_BELOW_THRESHOLD":
        r01 = _rule_by_id(rules, "R01")
        if r01 is None:
            return "NOT_CHECKED", f"The record says {code}; policy rule R01 not found."
        hit = evaluate_rule(r01, features)
        income = features.get("declared_income")
        threshold = float(r01["threshold"])
        inc_s = format_whole_number(income)
        thr_s = format_whole_number(threshold)
        if hit.fired:
            return (
                "CONSISTENT",
                format_reason_detail(
                    code,
                    f"declared income {inc_s} is below the {thr_s} threshold (rule R01).",
                ),
            )
        return (
            "UNSUPPORTED",
            format_reason_detail(
                code,
                f"declared income {inc_s} is at or above the {thr_s} threshold "
                f"(rule R01 not triggered).",
            ),
        )

    return "NOT_CHECKED", f"The record says reason_code {code}; check not implemented."


def check_recorded_reason(
    client: ZetarisClient,
    loan_id: str,
    *,
    ledger: Ledger | None = None,
) -> ReasonCheck:
    """SQL-grounded comparison of applications.reason_code to scorecard evidence."""
    evidence: list[ReasonCheckEvidence] = []
    app_res = _append_query(client, ledger, _reason_code_sql(client, loan_id), evidence)
    if not app_res.rows:
        return ReasonCheck(
            status="NOT_CHECKED",
            headline="Recorded reason not checked",
            recorded_reason_code="",
            detail=f"No application row for {loan_id}.",
            evidence=evidence,
        )
    row = app_res.rows[0]
    reason_code = str(row.get("reason_code") or "")
    outcome = str(row.get("outcome") or "")

    _append_query(client, ledger, _income_threshold_sql(client), evidence)

    from whyledger.counterfactual import fetch_loan_features, load_policy_rules

    features = fetch_loan_features(client, loan_id)
    rules = load_policy_rules(client)
    status, detail = _evaluate_reason_consistency(
        reason_code, features, rules, outcome
    )

    headline = "Recorded reason not supported by data"
    if status == "CONSISTENT":
        headline = "Recorded reason consistent with data"
    elif status == "NOT_CHECKED":
        headline = "Recorded reason check not applied"

    return ReasonCheck(
        status=status,
        headline=headline,
        recorded_reason_code=reason_code,
        detail=detail,
        evidence=evidence,
    )
