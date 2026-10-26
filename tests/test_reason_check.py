"""Recorded reason_code vs scorecard (planted LN-1301 mismatch)."""

from __future__ import annotations

from pathlib import Path

import pytest

from whyledger.reason_check import (
    check_recorded_reason,
    format_reason_detail,
    format_whole_number,
)
from whyledger.zetaris import StubZetarisClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"


@pytest.fixture(scope="module")
def stub() -> StubZetarisClient:
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out missing; run data/generate.py")
    return StubZetarisClient(data_dir=OUT)


def test_format_whole_number() -> None:
    assert format_whole_number(110000.0) == "110,000"
    assert format_whole_number(40000) == "40,000"


def test_format_reason_detail_no_duplicate_prefix() -> None:
    detail = format_reason_detail(
        "INCOME_BELOW_THRESHOLD",
        "the data shows declared income 110,000 is at or above the 40,000 threshold.",
    )
    assert detail.count("the data shows") == 1
    assert "the data shows the data shows" not in detail


def test_ln1301_recorded_reason_unsupported(stub: StubZetarisClient) -> None:
    result = check_recorded_reason(stub, "LN-1301")
    assert result.status == "UNSUPPORTED"
    assert result.recorded_reason_code == "INCOME_BELOW_THRESHOLD"
    assert "110,000" in result.detail
    assert "40,000" in result.detail
    assert "is at or above the 40,000 threshold" in result.detail
    assert ".0" not in result.detail
    assert result.detail.count("the data shows") == 1
    assert result.evidence
    assert all(ev.sha256 for ev in result.evidence)


def test_other_planted_loans_no_false_unsupported(stub: StubZetarisClient) -> None:
    for loan_id in ("LN-1042", "LN-1107", "LN-1230"):
        result = check_recorded_reason(stub, loan_id)
        assert result.status != "UNSUPPORTED", loan_id
