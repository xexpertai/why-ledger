"""Tests for Zetaris client: read-only guard, STUB sqlite, REST request shape."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from whyledger.zetaris import (
    QueryResult,
    ReadOnlySQLError,
    RestZetarisClient,
    StubZetarisClient,
    ZetarisAPIError,
    assert_readonly_sql,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"


# --- read-only guard ---------------------------------------------------------

GUARD_ACCEPT = [
    "SELECT 1",
    "select 1",
    "SELECT * FROM applications WHERE loan_id = 'LN-1042'",
    "SELECT * FROM applications WHERE note = 'SET aside'",
    "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
    "SHOW TABLES",
    "show tables",
    "SELECT 1;",  # trailing semicolon only
]

GUARD_REJECT = [
    "select 1; drop table x",
    "SELECT * FROM t -- ; DELETE",
    "SELECT * FROM t /* comment */",
    "DROP TABLE applications",
    "DELETE FROM applications",
    "INSERT INTO applications VALUES (1)",
    "UPDATE applications SET outcome = 'approve'",
    "CREATE TABLE evil (id INT)",
    "ALTER TABLE applications ADD COLUMN x INT",
    "TRUNCATE TABLE applications",
    "SELECT 1; SELECT 2",
    "EXPLAIN SELECT 1",
    "",
    "   ",
    "PRAGMA table_info(applications)",
    "ATTACH DATABASE 'x.db' AS other",
]


@pytest.mark.parametrize("sql", GUARD_ACCEPT)
def test_guard_accepts(sql: str) -> None:
    cleaned = assert_readonly_sql(sql)
    assert cleaned
    assert not cleaned.endswith(";")


@pytest.mark.parametrize("sql", GUARD_REJECT)
def test_guard_rejects(sql: str) -> None:
    with pytest.raises(ReadOnlySQLError):
        assert_readonly_sql(sql)


# --- STUB client -------------------------------------------------------------


@pytest.fixture(scope="module")
def stub() -> StubZetarisClient:
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out/applications.csv missing; run data/generate.py first")
    return StubZetarisClient(data_dir=OUT)


def test_stub_query_ln1042(stub: StubZetarisClient) -> None:
    result = stub.run_sql(
        "SELECT loan_id, outcome, reason_code FROM applications WHERE loan_id = 'LN-1042'"
    )
    assert isinstance(result, QueryResult)
    assert result.backend == "STUB-sqlite"
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["loan_id"] == "LN-1042"
    assert row["outcome"] == "decline"
    assert result.columns == ["loan_id", "outcome", "reason_code"]
    assert stub.query_log
    entry = stub.query_log[-1]
    assert entry.query_id == result.query_id
    assert entry.row_count == 1
    assert "LN-1042" in entry.sql


def test_stub_rejects_non_select(stub: StubZetarisClient) -> None:
    with pytest.raises(ReadOnlySQLError):
        stub.run_sql("DELETE FROM applications WHERE loan_id = 'LN-1042'")
    # Failed guard must not append a log entry for execution.
    assert all("DELETE" not in e.sql.upper() for e in stub.query_log)


def test_stub_marked_stub() -> None:
    assert "Stub" in StubZetarisClient.__name__
    assert "STUB" in (StubZetarisClient.__doc__ or "")
    import whyledger.zetaris as mod

    assert "STUB" in (mod.__doc__ or "")


# --- REST client (mocked, no network) ----------------------------------------


def test_rest_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_REST_URL", "http://zetaris.test/api/v1.0")
    monkeypatch.setenv("ZETARIS_ORG_ID", "org-9")
    monkeypatch.setenv("ZETARIS_API_KEY", "secret-key-do-not-log")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "columns": ["n"],
            "rows": [{"n": 1}],
        }
    }

    session = MagicMock()
    session.post.return_value = mock_resp

    client = RestZetarisClient(session=session, default_limit=50)
    result = client.run_sql("SELECT 1 AS n", limit=10)

    assert result.backend == "zetaris-rest"
    assert result.rows == [{"n": 1}]
    assert result.columns == ["n"]
    assert len(client.query_log) == 1
    assert client.query_log[0].row_count == 1

    session.post.assert_called_once()
    args, kwargs = session.post.call_args
    assert args[0] == "http://zetaris.test/api/v1.0/sql-editor/sqls/run"
    headers = kwargs["headers"]
    assert headers["Authorization"] == "Bearer secret-key-do-not-log"
    assert headers["X-Org-ID"] == "org-9"
    assert "X-Request-ID" in headers
    body = kwargs["json"]
    assert body["sql"] == "SELECT 1 AS n"
    assert body["source"] == "SqlEditor"
    assert body["limit"] == 10
    assert body["queryId"] == result.query_id

    # Secrets must never appear in repr.
    rep = repr(client)
    assert "secret-key-do-not-log" not in rep
    assert "api_key=***" in rep


def test_rest_default_base_url_and_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZETARIS_REST_URL", raising=False)
    monkeypatch.delenv("ZETARIS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ZETARIS_API_KEY"):
        RestZetarisClient(session=MagicMock())


def test_rest_clear_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_API_KEY", "k")
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"message": "unauthorized"}
    mock_resp.text = ""
    session = MagicMock()
    session.post.return_value = mock_resp
    client = RestZetarisClient(session=session)
    with pytest.raises(ZetarisAPIError, match="401"):
        client.run_sql("SELECT 1")


def test_rest_health_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_API_KEY", "k")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"rows": [{"ok": 1}]}}
    session = MagicMock()
    session.post.return_value = mock_resp
    client = RestZetarisClient(session=session)
    assert client.health_probe() is True


def test_rest_show_lightning_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_API_KEY", "k")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {"rows": [{"database_name": "WHY_LEDGER_DEMO"}]}
    }
    session = MagicMock()
    session.post.return_value = mock_resp
    client = RestZetarisClient(session=session)
    result = client.show_lightning_databases()
    assert "SHOW LIGHTNING DATABASES" in result.sql
    assert result.rows[0]["database_name"] == "WHY_LEDGER_DEMO"


def test_rest_guard_before_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_REST_URL", "http://zetaris.test/api/v1.0")
    monkeypatch.setenv("ZETARIS_ORG_ID", "1")
    monkeypatch.setenv("ZETARIS_API_KEY", "k")
    session = MagicMock()
    client = RestZetarisClient(session=session)
    with pytest.raises(ReadOnlySQLError):
        client.run_sql("select 1; drop table x")
    session.post.assert_not_called()
