"""Tests for Zetaris SQL dialect helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from whyledger.zetaris_dialect import adapt_sql, table_ref
from whyledger.zetaris_jdbc import JdbcZetarisClient


def test_table_ref_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZETARIS_LIVE", raising=False)
    monkeypatch.delenv("ZETARIS_API_KEY", raising=False)
    assert table_ref("applications", None) == "applications"


def test_table_ref_jdbc_client() -> None:
    client = MagicMock(spec=JdbcZetarisClient)
    assert table_ref("payments", client) == "WHY_LEDGER_SYN.payments"


def test_adapt_sql_qualifies_and_casts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_LIVE", "jdbc")
    raw = (
        "SELECT COUNT(*) FROM ( SELECT late_days FROM payments WHERE loan_id = 'x' ) t "
        "WHERE CAST(late_days AS INTEGER) > 15"
    )
    out = adapt_sql(raw)
    assert "WHY_LEDGER_SYN.payments" in out
    assert "CAST(late_days AS INT)" in out
