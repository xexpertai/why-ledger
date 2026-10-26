"""Applicant letter content for recorded-live Nightshift bundles (offline snapshots)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from whyledger.card import WhyCard
from whyledger.replay import (
    RECORDED_LIVE_NIGHTSHIFT_SUFFIX,
    Player,
    ReplayBundle,
    rebuild_applicant_letter,
)

ROOT = Path(__file__).resolve().parents[1]
REPLAY_DIR = ROOT / "replay"
SNAPSHOT_DIR = ROOT / "tests" / "snapshots"


def _nightshift_bundles() -> list[Path]:
    return sorted(REPLAY_DIR.glob(f"LN-*{RECORDED_LIVE_NIGHTSHIFT_SUFFIX}"))


@pytest.mark.parametrize("path", _nightshift_bundles(), ids=lambda p: p.stem)
def test_nightshift_bundle_letter_matches_rebuild(path: Path) -> None:
    if not path.is_file():
        pytest.skip("bundle missing")
    bundle = ReplayBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))
    expected = rebuild_applicant_letter(bundle)
    WhyCard.model_validate({**bundle.card, "applicant_letter": expected})
    stored = bundle.card.get("applicant_letter", "")
    assert stored == expected


def test_ln1230_letter_includes_top_amount_to_income_driver() -> None:
    path = REPLAY_DIR / "LN-1230.live-nightshift-planned.json"
    if not path.is_file():
        pytest.skip("bundle missing")
    session = Player(replay_dir=REPLAY_DIR).play_recorded_live("LN-1230")
    letter = session.card.applicant_letter
    assert "Amount relative to declared income" in letter
    assert "52.1%" in letter
    assert "times declared income" in letter


def test_ln1301_letter_unsupported_reason_and_enquiries() -> None:
    path = REPLAY_DIR / "LN-1301.live-nightshift-planned.json"
    if not path.is_file():
        pytest.skip("bundle missing")
    session = Player(replay_dir=REPLAY_DIR).play_recorded_live("LN-1301")
    letter = session.card.applicant_letter
    assert "recorded reason on the decision file was not supported" in letter
    assert "INCOME_BELOW_THRESHOLD" in letter
    assert "Credit enquiries" in letter
    assert "bureau score is 560" in letter.lower()


def test_letter_snapshots_on_disk_match_bundles() -> None:
    """tests/snapshots/*.letter.txt mirrors bundle letters for diff review."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for path in _nightshift_bundles():
        bundle = ReplayBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))
        letter = bundle.card.get("applicant_letter", "")
        snap = SNAPSHOT_DIR / f"{bundle.loan_id}.letter.txt"
        if snap.is_file():
            assert snap.read_text(encoding="utf-8") == letter
        else:
            snap.write_text(letter, encoding="utf-8")
