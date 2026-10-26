"""PLANTED verifier stress harness (offline STUB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from whyledger.verifier_stress import run_verifier_stress
from whyledger.zetaris import StubZetarisClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"


def test_planted_stress_expectations_on_stub() -> None:
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out missing")
    rows = run_verifier_stress(StubZetarisClient(data_dir=OUT))
    assert len(rows) == 14
    assert all(r.ok for r in rows)
    assert all(r.reason_matched for r in rows)
