"""Eval harness thresholds on STUB backends."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from whyledger.eval_harness import run_eval

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"
_STUB_ENV_KEYS = (
    "ZETARIS_LIVE",
    "ZETARIS_API_KEY",
    "METERLESS_RUNTIME_TOKEN",
    "NVIDIA_API_KEY",
    "MODEL_API_KEY",
    "LLM_ROUTE",
)


@pytest.fixture(scope="module")
def report() -> dict:
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out missing; run make data")
    saved = {k: os.environ.pop(k, None) for k in _STUB_ENV_KEYS}
    try:
        return run_eval(ROOT, sample_extra=6)
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def test_eval_stub_backends_labelled(report: dict) -> None:
    b = report["backends"]
    assert b["is_live"] is False
    assert b["zetaris"] == "STUB"
    assert "STUB" in b["banner"]


def test_eval_planted_top_driver_accuracy(report: dict) -> None:
    m = report["metrics"]
    assert m["planted_top_driver_accuracy"] == "4/4"
    assert m["planted_top_driver_accuracy_rate"] >= 1.0


def test_eval_zero_unsupported_claims_shipped_ln1042(report: dict) -> None:
    m = report["metrics"]
    assert m["ln1042_false_claim_rejected"] is True
    assert m["ln1042_zero_unsupported_shipped"] is True


def test_eval_verified_claim_rate(report: dict) -> None:
    assert report["metrics"]["verified_claim_rate"] >= 0.99


def test_eval_intent_cases(report: dict) -> None:
    rate = report["metrics"]["scout_intent_accuracy_rate"]
    assert rate >= 0.9


def test_eval_ln1230_counterfactual(report: dict) -> None:
    assert report["metrics"]["ln1230_counterfactual_ok"] is True


def test_eval_recorded_reason_flag_ln1301(report: dict) -> None:
    m = report["metrics"]
    assert m["planted_recorded_reason_mismatches_flagged"] == "1/1"
    assert m["recorded_reason_false_flags_other_planted"] == 0


def test_eval_draft_verification_note(report: dict) -> None:
    note = report.get("metric_notes", {}).get("drafted_verification_rate", "")
    assert "declared income below 20000" in note
