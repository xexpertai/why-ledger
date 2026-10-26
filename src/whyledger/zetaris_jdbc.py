"""Live Zetaris client via JayDeBeApi JDBC (Lightning driver).

One SQL statement per call; same read-only guard and query log as REST/STUB.
Credentials are read from the environment or the platform ``.env`` file without
ever being logged or included in repr.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from whyledger.zetaris import (
    QueryLogEntry,
    QueryResult,
    ReadOnlySQLError,
    _append_log,
    _new_query_id,
    _utc_now_iso,
    assert_readonly_sql,
)

_DEFAULT_JDBC_URL = "jdbc:zetaris:lightning@localhost:10000"
_DEFAULT_USER = "admin@zetaris.com"
_DEFAULT_DRIVER_JAR = Path.home() / "zetaris/zetaris-platform/jdbc/ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar"
_DRIVER_CLASS = "com.zetaris.lightning.jdbc.LightningDriver"


class ZetarisJDBCError(RuntimeError):
    """Raised when JDBC execution fails (message never includes secrets)."""


def _platform_env_path() -> Path:
    override = os.environ.get("ZETARIS_PLATFORM_ENV", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "zetaris/zetaris-platform/.env"


def _read_dotenv_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(f"{key}="):
            value = stripped.split("=", 1)[1].strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            return value
    return ""


def resolve_zetaris_jdbc_password() -> str:
    """Password for JDBC login (env first, then platform .env). Never log the result."""
    direct = os.environ.get("ZETARIS_JDBC_PASSWORD", "").strip()
    if direct:
        return direct
    direct = os.environ.get("ZETARIS_PASSWORD", "").strip()
    if direct:
        return direct
    return _read_dotenv_value(_platform_env_path(), "ZETARIS_ADMIN_PASSWORD")


class JdbcZetarisClient:
    """Live Zetaris via JDBC Lightning driver."""

    def __init__(
        self,
        *,
        jdbc_url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        driver_jar: str | Path | None = None,
        default_limit: int = 1000,
    ) -> None:
        self._jdbc_url = jdbc_url or os.environ.get("ZETARIS_JDBC_URL") or _DEFAULT_JDBC_URL
        self._user = user or os.environ.get("ZETARIS_JDBC_USER") or _DEFAULT_USER
        self._password = password if password is not None else resolve_zetaris_jdbc_password()
        jar = driver_jar or os.environ.get("ZETARIS_JDBC_JAR") or _DEFAULT_DRIVER_JAR
        self._driver_jar = str(Path(jar).expanduser())
        self.default_limit = default_limit
        self.query_log: list[QueryLogEntry] = []
        self._conn: Any = None
        if not self._password:
            raise ValueError("Zetaris JDBC password is required (ZETARIS_JDBC_PASSWORD or platform .env)")

    def __repr__(self) -> str:
        return (
            f"JdbcZetarisClient(jdbc_url={self._jdbc_url!r}, "
            f"user={self._user!r}, password=***)"
        )

    def _ensure_connection(self) -> Any:
        if self._conn is not None:
            return self._conn
        try:
            import jaydebeapi
        except ImportError as exc:
            raise ZetarisJDBCError(
                "jaydebeapi is not installed; pip install whyledger[live]"
            ) from exc
        jar_path = Path(self._driver_jar)
        if not jar_path.is_file():
            raise ZetarisJDBCError(f"JDBC driver jar not found: {jar_path}")
        self._conn = jaydebeapi.connect(
            _DRIVER_CLASS,
            self._jdbc_url,
            [self._user, self._password],
            self._driver_jar,
        )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def run_sql(self, sql: str, *, limit: int | None = None) -> QueryResult:
        cleaned = assert_readonly_sql(sql)
        return self._execute(cleaned, limit=limit, readonly=True)

    def execute_statement(self, sql: str, *, limit: int | None = None) -> QueryResult:
        """Run one setup/admin statement (DDL allowed; not for the agent path)."""
        cleaned = str(sql).strip()
        if cleaned.endswith(";"):
            cleaned = cleaned[:-1].rstrip()
        if not cleaned:
            raise ValueError("empty SQL")
        return self._execute(cleaned, limit=limit, readonly=False)

    def _execute(
        self, cleaned: str, *, limit: int | None, readonly: bool
    ) -> QueryResult:
        query_id = _new_query_id()
        lim = self.default_limit if limit is None else limit
        conn = self._ensure_connection()
        cur = conn.cursor()
        try:
            cur.execute(cleaned)
            if not readonly and not cur.description:
                columns = ["status"]
                rows = [{"status": "ok"}]
            else:
                columns, rows = _rows_from_jdbc_cursor(cur)
        except Exception as exc:
            msg = str(exc) or exc.__class__.__name__
            msg = re.sub(r"(?i)password=\S+", "password=***", msg)
            raise ZetarisJDBCError(msg) from exc
        finally:
            try:
                cur.close()
            except Exception:
                pass
        if lim is not None:
            rows = rows[:lim]
        result = QueryResult(
            query_id=query_id,
            sql=cleaned,
            columns=columns,
            rows=rows,
            backend="ZETARIS-JDBC",
        )
        _append_log(self.query_log, query_id=query_id, sql=cleaned, row_count=len(rows))
        return result

    def health_probe(self) -> bool:
        try:
            self.run_sql("SELECT 1 AS ok", limit=1)
            return True
        except Exception:
            return False


def _rows_from_jdbc_cursor(cur: Any) -> tuple[list[str], list[dict[str, Any]]]:
    if cur.description is None:
        return [], []
    columns = [str(col[0]) for col in cur.description]
    raw = cur.fetchall() or []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            rows.append(dict(item))
        elif isinstance(item, (list, tuple)):
            rows.append(dict(zip(columns, item)))
        else:
            rows.append({columns[0] if columns else "value": item})
    return columns, rows
