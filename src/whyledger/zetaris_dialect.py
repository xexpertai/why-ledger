"""SQL table qualification for live Zetaris Lightning databases."""

from __future__ import annotations

import os
import re

from whyledger.zetaris import RestZetarisClient, ZetarisClient

LIGHTNING_DB = "WHY_LEDGER_SYN"
_DEMO_TABLES = (
    "policy_rules",
    "policy_docs",
    "applications",
    "payments",
    "bureau",
)


def _live_zetaris_enabled() -> bool:
    flag = os.environ.get("ZETARIS_LIVE", "").strip().lower()
    if flag == "jdbc":
        return True
    if flag in ("1", "true", "yes") and os.environ.get("ZETARIS_API_KEY", "").strip():
        return True
    return False


def uses_lightning_tables(client: ZetarisClient | None = None) -> bool:
    if client is not None:
        from whyledger.zetaris_jdbc import JdbcZetarisClient

        if isinstance(client, (JdbcZetarisClient, RestZetarisClient)):
            return True
        return False
    return _live_zetaris_enabled()


def table_ref(name: str, client: ZetarisClient | None = None) -> str:
    """Return ``WHY_LEDGER_SYN.<table>`` on live Zetaris, else bare ``name`` (STUB sqlite)."""
    if uses_lightning_tables(client):
        return f"{LIGHTNING_DB}.{name}"
    return name


def adapt_sql(sql: str, client: ZetarisClient | None = None) -> str:
    """Rewrite STUB-style SQL (sqlite table names, casts) for live Lightning."""
    if not uses_lightning_tables(client):
        return sql
    out = sql
    for table in _DEMO_TABLES:
        out = re.sub(
            rf"(?<![.\w]){table}(?![.\w])",
            f"{LIGHTNING_DB}.{table}",
            out,
            flags=re.IGNORECASE,
        )
    out = out.replace("CAST(late_days AS INTEGER)", "CAST(late_days AS INT)")
    out = out.replace("CAST(late_days AS integer)", "CAST(late_days AS INT)")
    out = out.replace("CAST(declared_income AS REAL)", "CAST(declared_income AS DOUBLE)")
    out = out.replace("CAST(utilisation AS REAL)", "CAST(utilisation AS DOUBLE)")
    out = out.replace("CAST(a2.score AS REAL)", "CAST(a2.score AS DOUBLE)")
    out = out.replace("CAST(a1.score AS REAL)", "CAST(a1.score AS DOUBLE)")
    return out
