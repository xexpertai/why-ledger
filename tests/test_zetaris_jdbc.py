"""Unit tests for JDBC Zetaris client (mocked JPype/JayDeBeApi; no network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from whyledger.zetaris import QueryResult, ReadOnlySQLError
from whyledger.zetaris_jdbc import JdbcZetarisClient, ZetarisJDBCError, resolve_zetaris_jdbc_password


def test_resolve_password_prefers_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("ZETARIS_JDBC_PASSWORD", "from-env")
    monkeypatch.setenv("ZETARIS_PLATFORM_ENV", str(tmp_path / "missing.env"))
    assert resolve_zetaris_jdbc_password() == "from-env"


def test_resolve_password_from_platform_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("ZETARIS_JDBC_PASSWORD", raising=False)
    monkeypatch.delenv("ZETARIS_PASSWORD", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('ZETARIS_ADMIN_PASSWORD="file-secret"\n', encoding="utf-8")
    monkeypatch.setenv("ZETARIS_PLATFORM_ENV", str(env_file))
    assert resolve_zetaris_jdbc_password() == "file-secret"


@patch("jaydebeapi.connect")
def test_jdbc_run_sql(mock_connect: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_JDBC_PASSWORD", "pw")
    cur = MagicMock()
    cur.description = [("n",)]
    cur.fetchall.return_value = [(1,)]
    conn = MagicMock()
    conn.cursor.return_value = cur
    mock_connect.return_value = conn

    client = JdbcZetarisClient(password="pw", driver_jar=__file__)
    result = client.run_sql("SELECT 1 AS n", limit=10)

    assert isinstance(result, QueryResult)
    assert result.backend == "ZETARIS-JDBC"
    assert result.rows == [{"n": 1}]
    assert len(client.query_log) == 1
    cur.execute.assert_called_once_with("SELECT 1 AS n")
    rep = repr(client)
    assert "pw" not in rep
    assert "password=***" in rep


@patch("jaydebeapi.connect")
def test_jdbc_guard_before_execute(mock_connect: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_JDBC_PASSWORD", "pw")
    client = JdbcZetarisClient(password="pw", driver_jar=__file__)
    with pytest.raises(ReadOnlySQLError):
        client.run_sql("DROP TABLE x")
    mock_connect.assert_not_called()


@patch("jaydebeapi.connect")
def test_jdbc_execute_statement_allows_ddl(mock_connect: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_JDBC_PASSWORD", "pw")
    cur = MagicMock()
    cur.description = None
    conn = MagicMock()
    conn.cursor.return_value = cur
    mock_connect.return_value = conn

    client = JdbcZetarisClient(password="pw", driver_jar=__file__)
    result = client.execute_statement("CREATE LIGHTNING DATABASE demo")
    assert result.rows == [{"status": "ok"}]
    cur.execute.assert_called_once_with("CREATE LIGHTNING DATABASE demo")


@patch("jaydebeapi.connect")
def test_jdbc_maps_sql_errors(mock_connect: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZETARIS_JDBC_PASSWORD", "secret=leak")
    cur = MagicMock()
    cur.execute.side_effect = Exception("failed password=secret=leak")
    conn = MagicMock()
    conn.cursor.return_value = cur
    mock_connect.return_value = conn

    client = JdbcZetarisClient(password="pw", driver_jar=__file__)
    with pytest.raises(ZetarisJDBCError) as exc:
        client.run_sql("SELECT 1")
    assert "secret=leak" not in str(exc.value)
    assert "password=***" in str(exc.value)
