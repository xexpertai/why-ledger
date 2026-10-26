"""Append-only evidence ledger: claims bound to logged SQL evidence.

Canonical row hashing (documented for tests)
-------------------------------------------
``rows_sha256(rows)`` hashes the UTF-8 bytes of a canonical JSON encoding of
``rows`` (list of dicts). Rules:

1. Each cell value is normalised: ``bool`` stays bool; ``int`` stays int;
   ``float`` stays float (no int coercion); ``None`` stays null; everything
   else becomes ``str(value)``.
2. Each row becomes a JSON object with keys sorted lexicographically
   (``sort_keys=True``).
3. Rows are encoded independently with ``json.dumps(..., separators=(",", ":"),
   ensure_ascii=False)``, then the encoded strings are sorted lexicographically
   so **row order does not affect the hash**.
4. The canonical document is ``"[" + ",".join(sorted_encodings) + "]"``.
5. ``sha256`` is the lowercase hex digest of that UTF-8 byte string.

Evidence stores that hash at append time; the verifier re-runs SQL and
compares.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def _normalise_cell(value: Any) -> Any:
    """Normalise one cell for canonical JSON (see module docstring)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value
    return str(value)


def canonicalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return rows with normalised cells and sorted keys (order of rows kept)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({k: _normalise_cell(row[k]) for k in sorted(row.keys())})
    return out


def canonical_rows_json(rows: list[dict[str, Any]]) -> str:
    """Canonical JSON string for a row set (row-order independent)."""
    encodings: list[str] = []
    for row in canonicalize_rows(rows):
        encodings.append(
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
    encodings.sort()
    return "[" + ",".join(encodings) + "]"


def rows_sha256(rows: list[dict[str, Any]]) -> str:
    """SHA-256 hex digest of ``canonical_rows_json(rows)``."""
    payload = canonical_rows_json(rows).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class Claim:
    """A drafted assertion; valid only with evidence whose rows contain ``values``."""

    id: str
    text: str
    values: dict[str, int | float | str]
    query_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Evidence:
    """One logged query result bound into the ledger."""

    query_id: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    sha256: str


class Ledger:
    """Append-only store of claims and per-query evidence."""

    def __init__(self) -> None:
        self._claims: list[Claim] = []
        self._evidence: dict[str, Evidence] = {}

    @property
    def claims(self) -> list[Claim]:
        return list(self._claims)

    @property
    def evidence_map(self) -> dict[str, Evidence]:
        return dict(self._evidence)

    def append_evidence(
        self,
        *,
        query_id: str,
        sql: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        sha256: str | None = None,
    ) -> Evidence:
        """Append evidence for ``query_id``. Refuses overwrite (append-only)."""
        if query_id in self._evidence:
            raise ValueError(f"evidence already recorded for query_id={query_id!r}")
        digest = sha256 if sha256 is not None else rows_sha256(rows)
        # Store a shallow copy of rows so later mutation does not alter ledger.
        stored_rows = [dict(r) for r in rows]
        ev = Evidence(
            query_id=query_id,
            sql=sql,
            columns=list(columns),
            rows=stored_rows,
            sha256=digest,
        )
        self._evidence[query_id] = ev
        return ev

    def append_claim(self, claim: Claim) -> Claim:
        """Append a claim. Same id may not be re-appended."""
        if any(c.id == claim.id for c in self._claims):
            raise ValueError(f"claim already recorded for id={claim.id!r}")
        # Freeze a copy of mutable fields.
        frozen = Claim(
            id=claim.id,
            text=claim.text,
            values=dict(claim.values),
            query_ids=list(claim.query_ids),
        )
        self._claims.append(frozen)
        return frozen

    def get_claim(self, claim_id: str) -> Claim | None:
        for c in self._claims:
            if c.id == claim_id:
                return c
        return None

    def get_evidence(self, query_id: str) -> Evidence | None:
        return self._evidence.get(query_id)

    def evidence_for_claim(self, claim: Claim) -> list[Evidence]:
        out: list[Evidence] = []
        for qid in claim.query_ids:
            ev = self._evidence.get(qid)
            if ev is not None:
                out.append(ev)
        return out
