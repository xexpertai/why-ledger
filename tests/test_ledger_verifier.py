"""Tests for ledger hash canonicalisation and deterministic verifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from whyledger.ledger import Claim, Ledger, canonical_rows_json, rows_sha256
from whyledger.verifier import (
    check_evidence_loan_scope,
    verify_claim,
    verify_claims,
    zetaris_sql_failure_reason,
)
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


def test_hash_stable_across_row_order() -> None:
    """Row-order independence of rows_sha256 (see ledger module docstring)."""
    a = [
        {"b": 2, "a": 1},
        {"a": 3, "b": 4},
    ]
    b = [
        {"b": 4, "a": 3},
        {"a": 1, "b": 2},
    ]
    assert canonical_rows_json(a) == canonical_rows_json(b)
    assert rows_sha256(a) == rows_sha256(b)
    # Key order inside a row must not matter either.
    c = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert rows_sha256(a) == rows_sha256(c)


def test_hash_changes_when_cell_tampered() -> None:
    rows = [{"loan_id": "LN-1042", "late_count": 3}]
    tampered = [{"loan_id": "LN-1042", "late_count": 99}]
    assert rows_sha256(rows) != rows_sha256(tampered)


def test_tampered_stored_hash_detected(stub: StubZetarisClient) -> None:
    """Verifier re-runs SQL; stored hash that does not match live rows is UNSUPPORTED."""
    result = stub.run_sql(LATE_SQL)
    ledger = Ledger()
    # Deliberately store a wrong hash (tampered evidence).
    ledger.append_evidence(
        query_id=result.query_id,
        sql=result.sql,
        columns=result.columns,
        rows=result.rows,
        sha256="0" * 64,
    )
    claim = Claim(
        id="c-tamper",
        text="3 of last 6 payments on prior loan LN-0900 were over 15 days late",
        values={"late_count": 3},
        query_ids=[result.query_id],
    )
    ledger.append_claim(claim)
    outcome = verify_claim(
        claim, ledger, stub, subject_loan_id="LN-1042"
    )
    assert outcome.status == "UNSUPPORTED"
    assert "hash mismatch" in outcome.reason


def test_claim_without_query_unsupported() -> None:
    ledger = Ledger()
    claim = Claim(
        id="c-bare",
        text="something without evidence",
        values={"x": 1},
        query_ids=[],
    )
    ledger.append_claim(claim)
    # Client unused when no queries; pass a fresh stub if data exists else skip path.
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out missing")
    client = StubZetarisClient(data_dir=OUT)
    outcome = verify_claim(claim, ledger, client)
    assert outcome.status == "UNSUPPORTED"
    assert "no query" in outcome.reason.lower()


def test_ln1042_true_late_claim_verified_false_income_rejected(
    stub: StubZetarisClient,
) -> None:
    """Planted trap: false 'declared income below 20000' rejected; late claim VERIFIED."""
    late = stub.run_sql(LATE_SQL)
    income = stub.run_sql(INCOME_SQL)
    assert late.rows and int(float(late.rows[0]["late_count"])) == 3
    assert income.rows and float(income.rows[0]["declared_income"]) == 72000.0

    ledger = Ledger()
    ledger.append_evidence(
        query_id=late.query_id,
        sql=late.sql,
        columns=late.columns,
        rows=late.rows,
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
    by_id = {r.claim_id: r for r in results}

    assert by_id["c-late"].status == "VERIFIED"
    assert by_id["c-income-lie"].status == "UNSUPPORTED"
    assert "does not match column" in by_id["c-income-lie"].reason


class _StubZetarisSqlErrorOnSubstring:
    """STUB client: raises on SQL containing a forbidden column name."""

    def __init__(self, inner: StubZetarisClient, forbidden: str) -> None:
        self._inner = inner
        self._forbidden = forbidden
        self.query_log = inner.query_log

    def run_sql(self, sql: str, *, limit: int | None = None):
        if self._forbidden in sql and f"AS {self._forbidden}" not in sql.upper():
            raise RuntimeError(
                f"UNRESOLVED_COLUMN name `{self._forbidden}` cannot be resolved"
            )
        return self._inner.run_sql(sql, limit=limit)


def test_verifier_zetaris_sql_error_rejects_one_claim(stub: StubZetarisClient) -> None:
    """Bad claim SQL on re-run is UNSUPPORTED; other claims still verify."""
    good = stub.run_sql(LATE_SQL)
    bad_sql = (
        "SELECT bureau_score FROM bureau WHERE applicant_id = 'AP-1042'"
    )
    ledger = Ledger()
    ledger.append_evidence(
        query_id="q-bad",
        sql=bad_sql,
        columns=["bureau_score"],
        rows=[{"bureau_score": 670}],
        sha256=rows_sha256([{"bureau_score": 670}]),
    )
    ledger.append_evidence(
        query_id=good.query_id,
        sql=good.sql,
        columns=good.columns,
        rows=good.rows,
    )
    bad_claim = Claim(
        id="c-bad-sql",
        text="bureau score is 670",
        values={"bureau_score": 670},
        query_ids=["q-bad"],
    )
    good_claim = Claim(
        id="c-late",
        text="3 late payments on prior loan LN-0900",
        values={"late_count": 3},
        query_ids=[good.query_id],
    )
    client = _StubZetarisSqlErrorOnSubstring(stub, "bureau_score")
    results = verify_claims(
        [bad_claim, good_claim], ledger, client, subject_loan_id="LN-1042"
    )
    assert len(results) == 2
    assert results[0].status == "UNSUPPORTED"
    assert results[0].reason.startswith("claim SQL failed on Zetaris:")
    assert "bureau_score" in results[0].reason
    assert results[1].status == "VERIFIED"


def test_wrong_column_rejects_even_when_value_exists_elsewhere(
    stub: StubZetarisClient,
) -> None:
    """Value in another column must not verify under the wrong key."""
    income = stub.run_sql(INCOME_SQL)
    ledger = Ledger()
    ledger.append_evidence(
        query_id=income.query_id,
        sql=income.sql,
        columns=income.columns,
        rows=income.rows,
    )
    claim = Claim(
        id="c-wrong-col",
        text="late count is 72000",
        values={"late_count": 72000},
        query_ids=[income.query_id],
    )
    outcome = verify_claim(claim, ledger, stub)
    assert outcome.status == "UNSUPPORTED"
    assert "asserted column not in query result" in outcome.reason


def test_right_column_right_value_verified(stub: StubZetarisClient) -> None:
    income = stub.run_sql(INCOME_SQL)
    ledger = Ledger()
    ledger.append_evidence(
        query_id=income.query_id,
        sql=income.sql,
        columns=income.columns,
        rows=income.rows,
    )
    claim = Claim(
        id="c-income",
        text="declared income is 72000",
        values={"declared_income": 72000},
        query_ids=[income.query_id],
    )
    outcome = verify_claim(claim, ledger, stub)
    assert outcome.status == "VERIFIED"


def test_wrong_loan_scope_without_prior_label_rejected(stub: StubZetarisClient) -> None:
    """SQL on another loan_id is UNSUPPORTED unless claim names prior loan or peers."""
    late = stub.run_sql(LATE_SQL)
    ledger = Ledger()
    ledger.append_evidence(
        query_id=late.query_id,
        sql=late.sql,
        columns=late.columns,
        rows=late.rows,
    )
    bad_claim = Claim(
        id="c-bad-scope",
        text="late_count for loan LN-0900 is 3",
        values={"late_count": 3},
        query_ids=[late.query_id],
    )
    outcome = verify_claim(bad_claim, ledger, stub, subject_loan_id="LN-1042")
    assert outcome.status == "UNSUPPORTED"
    assert "different loan (LN-0900)" in outcome.reason


def test_prior_loan_scope_allowed_when_text_explicit(stub: StubZetarisClient) -> None:
    late = stub.run_sql(LATE_SQL)
    ledger = Ledger()
    ledger.append_evidence(
        query_id=late.query_id,
        sql=late.sql,
        columns=late.columns,
        rows=late.rows,
    )
    claim = Claim(
        id="c-prior",
        text="3 of last 6 payments on prior loan LN-0900 were over 15 days late",
        values={"late_count": 3},
        query_ids=[late.query_id],
    )
    reason = check_evidence_loan_scope(
        late.sql,
        claim,
        subject_loan_id="LN-1042",
        subject_applicant_id="AP-1042",
    )
    assert reason is None
    outcome = verify_claim(claim, ledger, stub, subject_loan_id="LN-1042")
    assert outcome.status == "VERIFIED"


def test_zetaris_sql_failure_reason_shortens_column_error() -> None:
    exc = RuntimeError(
        "UNRESOLVED_COLUMN name `bureau_score` cannot be resolved. Did you mean score?"
    )
    reason = zetaris_sql_failure_reason(exc)
    assert reason == "claim SQL failed on Zetaris: RuntimeError: bureau_score"


def test_ledger_append_only_rejects_duplicate_evidence(stub: StubZetarisClient) -> None:
    result = stub.run_sql("SELECT 1 AS n")
    ledger = Ledger()
    ledger.append_evidence(
        query_id=result.query_id,
        sql=result.sql,
        columns=result.columns,
        rows=result.rows,
    )
    with pytest.raises(ValueError, match="already recorded"):
        ledger.append_evidence(
            query_id=result.query_id,
            sql=result.sql,
            columns=result.columns,
            rows=result.rows,
        )
