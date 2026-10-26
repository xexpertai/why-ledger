"""Deterministic claim verifier: re-run cited SQL, hash-check, value-in-rows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from whyledger.ledger import Claim, Ledger, rows_sha256
from whyledger.zetaris import ZetarisClient

Status = Literal["VERIFIED", "UNSUPPORTED"]


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of verifying one claim."""

    claim_id: str
    status: Status
    reason: str


def _cell_matches(asserted: int | float | str, cell: Any) -> bool:
    """True if ``asserted`` appears as this cell (tolerant of str/number forms)."""
    if cell is None:
        return False
    if isinstance(asserted, str):
        return str(cell) == asserted
    # Numeric assertion: accept int/float/str representations of the same number.
    try:
        cell_num = float(cell)
    except (TypeError, ValueError):
        return False
    if isinstance(asserted, float):
        return abs(cell_num - asserted) < 1e-9
    # int asserted: also match 3.0 / "3"
    return cell_num == float(asserted) and float(asserted).is_integer()


def _result_column_names(rows: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for row in rows:
        names.update(row.keys())
    return names


def check_values_against_columns(
    values: dict[str, int | float | str],
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Return (unknown_columns, value_mismatches) for keys in ``values``.

    Each key must name a column present in the query result; the asserted value
    must equal that column in at least one returned row (numeric tolerance).
    """
    if not values:
        return [], []
    columns = _result_column_names(rows)
    unknown: list[str] = []
    mismatch: list[str] = []
    for name, asserted in values.items():
        if name not in columns:
            unknown.append(name)
            continue
        found = False
        for row in rows:
            if name in row and _cell_matches(asserted, row[name]):
                found = True
                break
        if not found:
            mismatch.append(name)
    return unknown, mismatch


def values_appear_in_rows(
    values: dict[str, int | float | str],
    rows: list[dict[str, Any]],
) -> list[str]:
    """Legacy helper: column keys with value mismatch (not unknown columns)."""
    unknown, mismatch = check_values_against_columns(values, rows)
    return unknown + mismatch


_UNRESOLVED_COL_RE = re.compile(
    r"name [`'\"]([^`'\"]+)[`'\"] cannot be resolved",
    re.IGNORECASE,
)

_LOAN_ID_EQ_RE = re.compile(r"loan_id\s*=\s*'([^']+)'", re.IGNORECASE)
_APPLICANT_ID_EQ_RE = re.compile(r"applicant_id\s*=\s*'([^']+)'", re.IGNORECASE)


def _is_peer_cases_sql(sql: str) -> bool:
    lowered = sql.lower()
    return "a2.loan_id" in lowered or " join " in lowered and " a2 " in lowered


def _claim_allows_prior_loan_evidence(claim: Claim, other_loan: str) -> bool:
    text = claim.text.lower()
    return "prior loan" in text and other_loan.upper() in claim.text.upper()


def _claim_allows_peer_evidence(claim: Claim) -> bool:
    return "peer" in claim.text.lower()


def check_evidence_loan_scope(
    sql: str,
    claim: Claim,
    *,
    subject_loan_id: str,
    subject_applicant_id: str | None = None,
) -> str | None:
    """Return UNSUPPORTED reason when SQL cites another loan/applicant without allowance."""
    if _is_peer_cases_sql(sql):
        return None
    subj_loan = subject_loan_id.upper()
    for loan_literal in _LOAN_ID_EQ_RE.findall(sql):
        lit = loan_literal.upper()
        if lit == subj_loan:
            continue
        if _claim_allows_peer_evidence(claim):
            continue
        if _claim_allows_prior_loan_evidence(claim, lit):
            continue
        return f"evidence is for a different loan ({lit})"
    if subject_applicant_id:
        subj_ap = subject_applicant_id.upper()
        sql_upper = sql.upper()
        for applicant_literal in _APPLICANT_ID_EQ_RE.findall(sql):
            if applicant_literal.upper() == subj_ap:
                continue
            if (
                f"LOAN_ID = '{subj_loan}'" in sql_upper
                or f"LOAN_ID='{subj_loan}'" in sql_upper.replace(" ", "")
            ):
                continue
            return f"evidence is for a different applicant ({applicant_literal.upper()})"
    return None


def resolve_subject_applicant_id(
    client: ZetarisClient,
    subject_loan_id: str,
) -> str | None:
    """Look up applicant_id for the case subject loan (best-effort)."""
    from whyledger.zetaris_dialect import table_ref

    lid = subject_loan_id.replace("'", "''")
    apps = table_ref("applications", client)
    sql = f"SELECT applicant_id FROM {apps} WHERE loan_id = '{lid}' LIMIT 1"
    try:
        result = client.run_sql(sql)
    except Exception:
        return None
    if not result.rows:
        return None
    raw = result.rows[0].get("applicant_id")
    return str(raw) if raw is not None else None


def zetaris_sql_failure_reason(exc: BaseException) -> str:
    """Human reason when claim SQL fails on Zetaris (no secrets)."""
    cls = exc.__class__.__name__
    msg = str(exc) or cls
    col = _UNRESOLVED_COL_RE.search(msg)
    if col:
        detail = f"{cls}: {col.group(1)}"
    elif "UNRESOLVED_COLUMN" in msg:
        detail = f"{cls}: UNRESOLVED_COLUMN"
    else:
        line = msg.splitlines()[0].strip()
        if len(line) > 100:
            line = line[:97] + "..."
        detail = f"{cls}: {line}" if line else cls
    return f"claim SQL failed on Zetaris: {detail}"


def verify_claim(
    claim: Claim,
    ledger: Ledger,
    client: ZetarisClient,
    *,
    subject_loan_id: str | None = None,
    subject_applicant_id: str | None = None,
) -> VerificationResult:
    """Re-run each cited query, compare sha256, require asserted values in rows.

    Claims with no ``query_ids`` are UNSUPPORTED. Missing ledger evidence,
    hash mismatch after re-execution, or asserted values absent from returned
    rows also yield UNSUPPORTED.
    """
    if not claim.query_ids:
        return VerificationResult(
            claim_id=claim.id,
            status="UNSUPPORTED",
            reason="no query evidence cited",
        )

    collected_rows: list[dict[str, Any]] = []
    scope_loan = subject_loan_id.upper() if subject_loan_id else None
    scope_applicant = subject_applicant_id
    if scope_loan and scope_applicant is None:
        scope_applicant = resolve_subject_applicant_id(client, scope_loan)

    for qid in claim.query_ids:
        evidence = ledger.get_evidence(qid)
        if evidence is None:
            return VerificationResult(
                claim_id=claim.id,
                status="UNSUPPORTED",
                reason=f"missing evidence for query_id={qid}",
            )

        if scope_loan:
            scope_reason = check_evidence_loan_scope(
                evidence.sql,
                claim,
                subject_loan_id=scope_loan,
                subject_applicant_id=scope_applicant,
            )
            if scope_reason:
                return VerificationResult(
                    claim_id=claim.id,
                    status="UNSUPPORTED",
                    reason=scope_reason,
                )

        try:
            result = client.run_sql(evidence.sql)
        except Exception as exc:
            return VerificationResult(
                claim_id=claim.id,
                status="UNSUPPORTED",
                reason=zetaris_sql_failure_reason(exc),
            )
        recomputed = rows_sha256(result.rows)
        if recomputed != evidence.sha256:
            return VerificationResult(
                claim_id=claim.id,
                status="UNSUPPORTED",
                reason=(
                    f"row hash mismatch for query_id={qid}: "
                    f"stored={evidence.sha256} recomputed={recomputed}"
                ),
            )
        collected_rows.extend(result.rows)

    unknown_cols, value_mismatch = check_values_against_columns(
        claim.values, collected_rows
    )
    if unknown_cols:
        return VerificationResult(
            claim_id=claim.id,
            status="UNSUPPORTED",
            reason=(
                "asserted column not in query result: "
                + ", ".join(unknown_cols)
            ),
        )
    if value_mismatch:
        return VerificationResult(
            claim_id=claim.id,
            status="UNSUPPORTED",
            reason=(
                "asserted value does not match column in evidence rows: "
                + ", ".join(value_mismatch)
            ),
        )

    return VerificationResult(
        claim_id=claim.id,
        status="VERIFIED",
        reason="hash matched and asserted values match named columns in re-run rows",
    )


def verify_claims(
    claims: list[Claim],
    ledger: Ledger,
    client: ZetarisClient,
    *,
    subject_loan_id: str | None = None,
) -> list[VerificationResult]:
    """Verify each claim in order; returns one result per claim."""
    subject_applicant_id: str | None = None
    if subject_loan_id:
        subject_applicant_id = resolve_subject_applicant_id(client, subject_loan_id)
    return [
        verify_claim(
            c,
            ledger,
            client,
            subject_loan_id=subject_loan_id,
            subject_applicant_id=subject_applicant_id,
        )
        for c in claims
    ]
