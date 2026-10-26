"""Live Zetaris JDBC integration (skipped unless ZETARIS_LIVE=jdbc)."""

from __future__ import annotations

import os

import pytest

from whyledger.zetaris_jdbc import JdbcZetarisClient


@pytest.mark.live
def test_jdbc_select_one() -> None:
    if os.environ.get("ZETARIS_LIVE", "").strip().lower() != "jdbc":
        pytest.skip("set ZETARIS_LIVE=jdbc to run live JDBC tests")
    client = JdbcZetarisClient()
    try:
        result = client.run_sql("SELECT 1 AS ok", limit=1)
        assert result.backend == "ZETARIS-JDBC"
        assert result.rows and result.rows[0].get("ok") == 1
    finally:
        client.close()
