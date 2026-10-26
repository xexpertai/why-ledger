"""PLANTED verifier stress claims (not model output).

Fixed tampered claims per planted loan for honest verifier evaluation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from whyledger.ledger import Claim, Ledger, rows_sha256
from whyledger.verifier import VerificationResult, verify_claim
from whyledger.zetaris import ZetarisClient
from whyledger.zetaris_dialect import table_ref

_STRESS_LABEL = "PLANTED test claims, not model output"


@dataclass(frozen=True)
class PlantedStressCase:
    """One labelled claim for the verifier stress harness."""

    loan_id: str
    claim_id: str
    text: str
    sql_key: str
    values: dict[str, int | float | str]
    planted_truth: str
    expect: Literal["VERIFIED", "UNSUPPORTED"]
    expected_reason: str


def _apps(loan_id: str, client: ZetarisClient) -> str:
    return table_ref("applications", client)


def _bureau(client: ZetarisClient) -> str:
    return table_ref("bureau", client)


def planted_stress_cases() -> list[PlantedStressCase]:
    """Return the fixed PLANTED claim matrix (SYNTHETIC loans only)."""
    return [
        PlantedStressCase(
            loan_id="LN-1042",
            claim_id="stress-ln1042-wrong-loan-scope",
            text="late_count is 3 (SQL filters prior loan without prior-loan wording)",
            sql_key="late_count",
            values={"late_count": 3},
            planted_truth="wrong loan scope: SQL uses LN-0900 but claim omits prior-loan label",
            expect="UNSUPPORTED",
            expected_reason="evidence is for a different loan",
        ),
        PlantedStressCase(
            loan_id="LN-1042",
            claim_id="stress-ln1042-true-prior-late",
            text="3 late payments on prior loan LN-0900 (SYNTHETIC planted true)",
            sql_key="late_count",
            values={"late_count": 3},
            planted_truth="true: prior-loan conduct with explicit prior-loan text",
            expect="VERIFIED",
            expected_reason="hash matched",
        ),
        PlantedStressCase(
            loan_id="LN-1042",
            claim_id="stress-ln1042-false-income",
            text="declared income is 20000 (SYNTHETIC planted false)",
            sql_key="income",
            values={"declared_income": 20000},
            planted_truth="false: declared_income is 72000",
            expect="UNSUPPORTED",
            expected_reason="asserted value does not match column",
        ),
        PlantedStressCase(
            loan_id="LN-1042",
            claim_id="stress-ln1042-wrong-column",
            text="utilisation value under late_count key (wrong column)",
            sql_key="late_count",
            values={"late_count": 91},
            planted_truth="wrong column: 91 is utilisation, not late_count",
            expect="UNSUPPORTED",
            expected_reason="asserted value does not match column",
        ),
        PlantedStressCase(
            loan_id="LN-1042",
            claim_id="stress-ln1042-bad-column",
            text="non-existent column name",
            sql_key="late_count",
            values={"phantom_metric": 3},
            planted_truth="non-existent column phantom_metric",
            expect="UNSUPPORTED",
            expected_reason="asserted column not in query result",
        ),
        PlantedStressCase(
            loan_id="LN-1107",
            claim_id="stress-ln1107-true-outcome",
            text="outcome approve (SYNTHETIC planted true)",
            sql_key="outcome",
            values={"outcome": "approve"},
            planted_truth="true: outcome column is approve",
            expect="VERIFIED",
            expected_reason="hash matched",
        ),
        PlantedStressCase(
            loan_id="LN-1107",
            claim_id="stress-ln1107-false-defaults",
            text="defaults is 0 (SYNTHETIC planted false)",
            sql_key="defaults",
            values={"defaults": 0},
            planted_truth="false: defaults is 2",
            expect="UNSUPPORTED",
            expected_reason="asserted value does not match column",
        ),
        PlantedStressCase(
            loan_id="LN-1107",
            claim_id="stress-ln1107-wrong-column",
            text="defaults count under outcome key",
            sql_key="outcome",
            values={"outcome": 2},
            planted_truth="wrong column: 2 is defaults, not outcome",
            expect="UNSUPPORTED",
            expected_reason="asserted value does not match column",
        ),
        PlantedStressCase(
            loan_id="LN-1230",
            claim_id="stress-ln1230-true-score",
            text="scorecard points 137 (SYNTHETIC planted true)",
            sql_key="score",
            values={"scorecard_points": 137},
            planted_truth="true: scorecard_points alias column is 137",
            expect="VERIFIED",
            expected_reason="hash matched",
        ),
        PlantedStressCase(
            loan_id="LN-1230",
            claim_id="stress-ln1230-false-score",
            text="scorecard points 200 (SYNTHETIC planted false)",
            sql_key="score",
            values={"scorecard_points": 200},
            planted_truth="false: scorecard_points is 137",
            expect="UNSUPPORTED",
            expected_reason="asserted value does not match column",
        ),
        PlantedStressCase(
            loan_id="LN-1230",
            claim_id="stress-ln1230-bad-sql",
            text="SQL uses display alias bureau_score without AS (planted bad SQL)",
            sql_key="bad_bureau_score",
            values={"bureau_score": 640},
            planted_truth="bad SQL: bureau_score is not a physical column",
            expect="UNSUPPORTED",
            expected_reason="Zetaris",
        ),
        PlantedStressCase(
            loan_id="LN-1301",
            claim_id="stress-ln1301-true-enquiries",
            text="enquiries_6m is 7 (SYNTHETIC planted true)",
            sql_key="enquiries",
            values={"enquiries_6m": 7},
            planted_truth="true: enquiries_6m column is 7",
            expect="VERIFIED",
            expected_reason="hash matched",
        ),
        PlantedStressCase(
            loan_id="LN-1301",
            claim_id="stress-ln1301-wrong-column",
            text="bureau score under enquiries_6m key",
            sql_key="enquiries",
            values={"enquiries_6m": 560},
            planted_truth="wrong column: 560 is bureau score, not enquiries",
            expect="UNSUPPORTED",
            expected_reason="asserted value does not match column",
        ),
        PlantedStressCase(
            loan_id="LN-1301",
            claim_id="stress-ln1301-fake-column",
            text="fictitious column credit_tier",
            sql_key="enquiries",
            values={"credit_tier": "low"},
            planted_truth="non-existent column credit_tier",
            expect="UNSUPPORTED",
            expected_reason="asserted column not in query result",
        ),
    ]


def _late_count_for_subject_sql(loan_id: str, client: ZetarisClient) -> str:
    """Late_count on the subject loan's prior_loan_id (SQL cites subject loan_id)."""
    lid = loan_id.replace("'", "''")
    payments = table_ref("payments", client)
    apps = table_ref("applications", client)
    return (
        "SELECT COUNT(*) AS late_count FROM ("
        f" SELECT late_days FROM {payments} WHERE loan_id = ("
        f" SELECT prior_loan_id FROM {apps} WHERE loan_id = '{lid}'"
        " ) ORDER BY due_date DESC LIMIT 6"
        ") t WHERE CAST(late_days AS INTEGER) > 15"
    )


def _sql_for_case(case: PlantedStressCase, client: ZetarisClient) -> str:
    lid = case.loan_id
    apps = _apps(lid, client)
    bureau = _bureau(client)
    key = case.sql_key
    if key == "late_count":
        if case.claim_id == "stress-ln1042-wrong-loan-scope":
            payments = table_ref("payments", client)
            return (
                "SELECT COUNT(*) AS late_count FROM ("
                f" SELECT late_days FROM {payments} WHERE loan_id = 'LN-0900'"
                " ORDER BY due_date DESC LIMIT 6"
                ") t WHERE CAST(late_days AS INTEGER) > 15"
            )
        return _late_count_for_subject_sql(lid, client)
    if key == "income":
        return (
            f"SELECT CAST(declared_income AS REAL) AS declared_income"
            f" FROM {apps} WHERE loan_id = '{lid}'"
        )
    if key == "outcome":
        return f"SELECT outcome FROM {apps} WHERE loan_id = '{lid}'"
    if key == "defaults":
        return (
            f"SELECT defaults FROM {bureau} WHERE applicant_id = 'AP-1107'"
        )
    if key == "score":
        return (
            f"SELECT score AS scorecard_points FROM {apps} WHERE loan_id = '{lid}'"
        )
    if key == "bad_bureau_score":
        return f"SELECT bureau_score FROM {bureau} WHERE applicant_id = 'AP-1230'"
    if key == "enquiries":
        return (
            f"SELECT enquiries_6m FROM {bureau} WHERE applicant_id = 'AP-1301'"
        )
    raise ValueError(f"unknown sql_key {key!r}")


@dataclass
class StressRunRow:
    loan_id: str
    claim: str
    planted_truth: str
    verdict: str
    reason: str
    expect: str
    ok: bool
    expected_reason: str
    reason_matched: bool


def run_verifier_stress(client: ZetarisClient) -> list[StressRunRow]:
    """Run all PLANTED cases through the verifier."""
    rows: list[StressRunRow] = []
    for case in planted_stress_cases():
        sql = _sql_for_case(case, client)
        ledger = Ledger()
        if case.sql_key == "bad_bureau_score":
            qid = f"stress-{case.claim_id}"
            ev_rows = [{"bureau_score": case.values.get("bureau_score", 640)}]
            ledger.append_evidence(
                query_id=qid,
                sql=sql,
                columns=["bureau_score"],
                rows=ev_rows,
                sha256=rows_sha256(ev_rows),
            )
            query_id = qid
        else:
            result = client.run_sql(sql)
            ledger.append_evidence(
                query_id=result.query_id,
                sql=result.sql,
                columns=result.columns,
                rows=result.rows,
            )
            query_id = result.query_id
        claim = Claim(
            id=case.claim_id,
            text=case.text,
            values=dict(case.values),
            query_ids=[query_id],
        )
        ledger.append_claim(claim)
        outcome = verify_claim(
            claim,
            ledger,
            client,
            subject_loan_id=case.loan_id,
        )
        reason_matched = case.expected_reason in outcome.reason
        rows.append(
            StressRunRow(
                loan_id=case.loan_id,
                claim=case.text,
                planted_truth=case.planted_truth,
                verdict=outcome.status,
                reason=outcome.reason,
                expect=case.expect,
                ok=outcome.status == case.expect,
                expected_reason=case.expected_reason,
                reason_matched=reason_matched,
            )
        )
    return rows


def stress_backend_label(client: ZetarisClient) -> str:
    return type(client).__name__


def write_stress_artifacts(
    rows: list[StressRunRow],
    *,
    backend: str,
    out_md: Path,
    out_json: Path,
) -> dict[str, Any]:
    """Write eval/verifier-stress.md and JSON snapshot."""
    verified = sum(1 for r in rows if r.verdict == "VERIFIED")
    unsupported = sum(1 for r in rows if r.verdict == "UNSUPPORTED")
    expect_ok = sum(1 for r in rows if r.ok)
    reason_ok = sum(1 for r in rows if r.reason_matched)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict[str, Any] = {
        "label": _STRESS_LABEL,
        "is_synthetic": True,
        "recorded_at": ts,
        "backend": backend,
        "totals": {
            "cases": len(rows),
            "verified": verified,
            "unsupported": unsupported,
            "expect_matched": expect_ok,
            "reason_matched": reason_ok,
        },
        "rows": [asdict(r) for r in rows],
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# Verifier stress test ({_STRESS_LABEL})",
        "",
        f"**Recorded:** {ts}",
        f"**Zetaris backend:** {backend}",
        "",
        "| Loan | Claim | Planted truth | Verdict | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        reason = r.reason.replace("|", "\\|")
        if len(reason) > 120:
            reason = reason[:117] + "..."
        claim = r.claim.replace("|", "\\|")
        truth = r.planted_truth.replace("|", "\\|")
        lines.append(
            f"| {r.loan_id} | {claim} | {truth} | {r.verdict} | {reason} |"
        )
    lines.extend(
        [
            "",
            "## Totals",
            "",
            f"- Cases: {len(rows)}",
            f"- VERIFIED: {verified}",
            f"- UNSUPPORTED: {unsupported}",
            f"- Verdict matched: {expect_ok}/{len(rows)}",
            f"- Reason matched: {reason_ok}/{len(rows)}",
            "",
            f"_{_STRESS_LABEL}_",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def load_stress_json(path: Path | None = None) -> dict[str, Any] | None:
    p = path or Path(__file__).resolve().parents[2] / "eval" / "verifier-stress.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
