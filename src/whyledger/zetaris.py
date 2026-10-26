"""Zetaris SQL client: REST live path and STUB sqlite path.

Read-only guard allows a single SELECT / WITH...SELECT / SHOW. Every query is
logged on the client (id, timestamp, sql, row_count). Secrets from env are
never logged or included in repr.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import requests

# Default SYNTHETIC demo tables (gitignored data/out/).
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "out"
_DEFAULT_REST_URL = "http://localhost:8888/api/v1.0"


class ZetarisAPIError(RuntimeError):
    """Raised when the Zetaris REST API returns an error (message never includes secrets)."""

_COMMENT_MARKERS = re.compile(r"--|/\*|#")
_STRING_LITERALS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_FORBIDDEN_KW = re.compile(
    r"\b(?:"
    r"INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|GRANT|REVOKE|"
    r"ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|EXEC(?:UTE)?|CALL|MERGE|COPY|LOAD|"
    r"IMPORT|EXPORT|UPSERT|RENAME|BEGIN|COMMIT|ROLLBACK|TRANSACTION|"
    r"INTO|HANDLER|DO|SET|USE|ANALYZE|OPTIMIZE|FLUSH|KILL|LOCK|UNLOCK|"
    r"DECLARE|FETCH|OPEN|CLOSE|PREPARE|DEALLOCATE"
    r")\b",
    re.IGNORECASE,
)


class ReadOnlySQLError(ValueError):
    """Raised when SQL fails the read-only guard."""


@dataclass(frozen=True)
class QueryResult:
    """Result of one guarded SQL run."""

    query_id: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    backend: str


@dataclass(frozen=True)
class QueryLogEntry:
    """One entry in the per-client query log."""

    query_id: str
    timestamp: str
    sql: str
    row_count: int


def _mask_string_literals(sql: str) -> str:
    """Replace quoted literals with spaces so keyword checks ignore string contents."""
    return _STRING_LITERALS.sub(lambda m: " " * len(m.group(0)), sql)


def assert_readonly_sql(sql: str) -> str:
    """Accept a single SELECT / WITH...SELECT / SHOW; reject everything else.

    Rejects semicolon-chained statements, DDL/DML keywords, and comment markers
    (including tricks like ``SELECT * FROM t -- ; DELETE``).
    Returns the stripped SQL (trailing semicolon removed).
    """
    if sql is None or not str(sql).strip():
        raise ReadOnlySQLError("empty SQL is not allowed")

    cleaned = str(sql).strip()
    # Comment markers anywhere (including after code) are treated as injection tricks.
    if _COMMENT_MARKERS.search(cleaned):
        raise ReadOnlySQLError("SQL comments are not allowed")

    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()

    masked = _mask_string_literals(cleaned)
    if ";" in masked:
        raise ReadOnlySQLError("multiple / semicolon-chained statements are not allowed")

    if _FORBIDDEN_KW.search(masked):
        raise ReadOnlySQLError("DDL/DML or other non-readonly keyword is not allowed")

    parts = cleaned.split(None, 1)
    first = parts[0].upper()
    if first not in ("SELECT", "WITH", "SHOW"):
        raise ReadOnlySQLError("only SELECT, WITH...SELECT, or SHOW statements are allowed")
    if first == "WITH" and not re.search(r"\bSELECT\b", masked, re.IGNORECASE):
        raise ReadOnlySQLError("WITH statement must include SELECT")

    return cleaned


@runtime_checkable
class ZetarisClient(Protocol):
    """Protocol: run read-only SQL and return list-of-dict rows via QueryResult."""

    query_log: list[QueryLogEntry]

    def run_sql(self, sql: str, *, limit: int | None = None) -> QueryResult:
        """Execute one read-only statement; rows are QueryResult.rows (list[dict])."""
        ...


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_query_id() -> str:
    return str(uuid.uuid4())


def _append_log(
    log: list[QueryLogEntry], *, query_id: str, sql: str, row_count: int
) -> None:
    log.append(
        QueryLogEntry(
            query_id=query_id,
            timestamp=_utc_now_iso(),
            sql=sql,
            row_count=row_count,
        )
    )


def _rows_from_cursor(cur: sqlite3.Cursor) -> tuple[list[str], list[dict[str, Any]]]:
    columns = [d[0] for d in (cur.description or [])]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    return columns, rows


class RestZetarisClient:
    """Live Zetaris client: POST {ZETARIS_REST_URL}/sql-editor/sqls/run."""

    def __init__(
        self,
        *,
        rest_url: str | None = None,
        org_id: str | None = None,
        api_key: str | None = None,
        default_limit: int = 1000,
        session: requests.Session | None = None,
    ) -> None:
        self._rest_url = (
            rest_url or os.environ.get("ZETARIS_REST_URL") or _DEFAULT_REST_URL
        ).rstrip("/")
        self._org_id = org_id if org_id is not None else os.environ.get("ZETARIS_ORG_ID", "1")
        self._api_key = api_key if api_key is not None else os.environ.get("ZETARIS_API_KEY", "")
        self.default_limit = default_limit
        self._session = session or requests.Session()
        self.query_log: list[QueryLogEntry] = []
        if not self._api_key:
            raise ValueError("ZETARIS_API_KEY is required for RestZetarisClient")

    def __repr__(self) -> str:
        # Never include the API key.
        return (
            f"RestZetarisClient(rest_url={self._rest_url!r}, "
            f"org_id={self._org_id!r}, api_key=***)"
        )

    def run_sql(self, sql: str, *, limit: int | None = None) -> QueryResult:
        cleaned = assert_readonly_sql(sql)
        return self._post_sql(cleaned, limit=limit)

    def execute_statement(self, sql: str, *, limit: int | None = None) -> QueryResult:
        """POST one setup/admin statement (DDL allowed; not for the agent path)."""
        cleaned = str(sql).strip()
        if cleaned.endswith(";"):
            cleaned = cleaned[:-1].rstrip()
        if not cleaned:
            raise ValueError("empty SQL")
        return self._post_sql(cleaned, limit=limit)

    def _post_sql(self, cleaned: str, *, limit: int | None = None) -> QueryResult:
        query_id = _new_query_id()
        request_id = str(uuid.uuid4())
        lim = self.default_limit if limit is None else limit
        url = f"{self._rest_url}/sql-editor/sqls/run"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-Org-ID": str(self._org_id),
            "X-Request-ID": request_id,
            "Content-Type": "application/json",
        }
        payload = {
            "queryId": query_id,
            "sql": cleaned,
            "source": "SqlEditor",
            "limit": lim,
        }
        # Do not log headers/payload secrets — only sql + ids go to query_log.
        resp = self._session.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code >= 400:
            raise _http_error_from_response(resp)
        columns, rows = _parse_rest_response(resp.json())
        if lim is not None:
            rows = rows[:lim]
        result = QueryResult(
            query_id=query_id,
            sql=cleaned,
            columns=columns,
            rows=rows,
            backend="zetaris-rest",
        )
        _append_log(self.query_log, query_id=query_id, sql=cleaned, row_count=len(rows))
        return result

    def health_probe(self) -> bool:
        """Return True if a trivial SELECT succeeds against the REST API."""
        try:
            self.run_sql("SELECT 1 AS ok", limit=1)
            return True
        except Exception:
            return False

    def show_lightning_databases(self) -> QueryResult:
        """Run Lightning SQL ``SHOW LIGHTNING DATABASES`` (read-only metadata)."""
        return self.run_sql("SHOW LIGHTNING DATABASES")


def _http_error_from_response(resp: requests.Response) -> ZetarisAPIError:
    detail = f"HTTP {resp.status_code}"
    try:
        body = resp.json()
        if isinstance(body, dict):
            for key in ("message", "error", "detail", "title"):
                if body.get(key):
                    detail = f"{detail}: {body[key]}"
                    break
    except ValueError:
        text = (resp.text or "").strip()
        if text and len(text) <= 500:
            detail = f"{detail}: {text}"
    return ZetarisAPIError(detail)


def _parse_rest_response(body: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Best-effort parse of Zetaris /sql-editor/sqls/run JSON into columns + row dicts."""
    if not isinstance(body, dict):
        raise ZetarisAPIError("unexpected Zetaris response: expected JSON object")

    if body.get("success") is False:
        msg = body.get("message") or body.get("error") or "Zetaris query failed"
        raise ZetarisAPIError(str(msg))

    data = body.get("data", body)
    columns: list[str] = []
    raw_rows: list[Any] = []

    if isinstance(data, dict):
        columns = list(data.get("columns") or data.get("columnNames") or [])
        raw_rows = data.get("rows") or data.get("data") or data.get("records") or []
        if not raw_rows and isinstance(data.get("result"), list):
            raw_rows = data["result"]
    elif isinstance(data, list):
        raw_rows = data

    if not isinstance(raw_rows, list):
        raise ValueError("unexpected Zetaris response: rows not a list")

    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if isinstance(item, dict):
            rows.append(dict(item))
            if not columns:
                columns = list(item.keys())
        elif isinstance(item, (list, tuple)):
            if not columns:
                columns = [f"c{i}" for i in range(len(item))]
            rows.append(dict(zip(columns, item)))
        else:
            rows.append({"value": item})
            if not columns:
                columns = ["value"]

    if not columns and rows:
        columns = list(rows[0].keys())
    return columns, rows


class StubZetarisClient:
    """STUB Zetaris backend: in-memory sqlite3 loaded from data/out/ CSV/JSON.

    STUB — for Replay / offline tests only. Not a live Zetaris connection.
    Result metadata backend is always ``STUB-sqlite``.
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        default_limit: int = 1000,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
        self.default_limit = default_limit
        self.query_log: list[QueryLogEntry] = []
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._load_tables()

    def __repr__(self) -> str:
        return f"StubZetarisClient(data_dir={str(self.data_dir)!r}, backend='STUB-sqlite')"

    def _load_tables(self) -> None:
        """STUB: load CSV/JSON files from data_dir into sqlite tables."""
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"STUB data dir not found: {self.data_dir}")

        for csv_path in sorted(self.data_dir.glob("*.csv")):
            self._load_csv(csv_path.stem, csv_path)

        bureau_path = self.data_dir / "bureau.json"
        if bureau_path.exists():
            payload = json.loads(bureau_path.read_text(encoding="utf-8"))
            rows = payload.get("rows", payload if isinstance(payload, list) else [])
            self._load_dicts("bureau", rows)

        chunks_path = self.data_dir / "policy_docs_chunks.json"
        if chunks_path.exists():
            chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            if isinstance(chunks, list):
                self._load_dicts("policy_docs", chunks)

    def _load_csv(self, table: str, path: Path) -> None:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        self._load_dicts(table, rows)

    def _load_dicts(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            self._conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (placeholder TEXT)')
            self._conn.commit()
            return
        # Preserve column order from the first row.
        columns = list(rows[0].keys())
        col_sql = ", ".join(f'"{c}" TEXT' for c in columns)
        self._conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        self._conn.execute(f'CREATE TABLE "{table}" ({col_sql})')
        placeholders = ", ".join("?" for _ in columns)
        insert_sql = f'INSERT INTO "{table}" VALUES ({placeholders})'
        for row in rows:
            values = ["" if row.get(c) is None else str(row.get(c)) for c in columns]
            self._conn.execute(insert_sql, values)
        self._conn.commit()

    def run_sql(self, sql: str, *, limit: int | None = None) -> QueryResult:
        """STUB: execute guarded SQL against the in-memory sqlite copy of data/out/."""
        cleaned = assert_readonly_sql(sql)
        query_id = _new_query_id()
        lim = self.default_limit if limit is None else limit
        cur = self._conn.execute(cleaned)
        columns, rows = _rows_from_cursor(cur)
        if lim is not None:
            rows = rows[:lim]
        result = QueryResult(
            query_id=query_id,
            sql=cleaned,
            columns=columns,
            rows=rows,
            backend="STUB-sqlite",
        )
        _append_log(self.query_log, query_id=query_id, sql=cleaned, row_count=len(rows))
        return result
