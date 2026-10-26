"""Replay recorder/player and API replay mode (no data/out)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whyledger.api import _cards, create_app
from whyledger.card import WhyCard
from whyledger.replay import (
    Player,
    Recorder,
    ReplayBundle,
    cards_equal_except_mode,
    format_original_latency_seconds,
    format_recorded_at_minute,
    recorded_live_banner_text,
    recorded_live_is_nightshift,
    record_investigator_run,
)
from whyledger.agent import Investigator
from whyledger.llm import RecordedStubLLM
from whyledger.zetaris import StubZetarisClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"
FIXTURE = ROOT / "fixtures" / "llm_ln1042.json"
REPLAY_DIR = ROOT / "replay"


@pytest.fixture()
def stub() -> StubZetarisClient:
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out missing; run data/generate.py")
    return StubZetarisClient(data_dir=OUT)


def test_recorder_player_round_trip(stub: StubZetarisClient, tmp_path: Path) -> None:
    llm = RecordedStubLLM(fixture_path=FIXTURE)
    inv = Investigator(loan_id="LN-1042", client=stub, llm=llm, mode="stub")
    result = inv.run()
    assert result.card is not None
    recorder = Recorder(replay_dir=tmp_path)
    path = record_investigator_run(inv, result=result, client=stub, recorder=recorder)
    assert path.is_file()

    player = Player(replay_dir=tmp_path)
    session = player.play("LN-1042")
    assert session.card.mode == "replay"
    recorded = WhyCard.model_validate(json.loads(path.read_text())["card"])
    assert cards_equal_except_mode(recorded, session.card)
    assert session.ledger.evidence_map
    assert all(ev.sha256 for ev in session.ledger.evidence_map.values())


def test_recorded_live_player_ln1230() -> None:
    nightshift = REPLAY_DIR / "LN-1230.live-nightshift-planned.json"
    legacy = REPLAY_DIR / "LN-1230.live-nemotron-planned.json"
    if not nightshift.is_file() and not legacy.is_file():
        pytest.skip("recorded live bundle missing")
    session = Player(replay_dir=REPLAY_DIR).play_recorded_live("LN-1230")
    assert session.replay_source == "recorded_live"
    assert session.card.mode == "replay"
    banner = recorded_live_banner_text(session.bundle)
    assert "original latency" in banner
    if recorded_live_is_nightshift(session.bundle):
        assert "via Meterless Nightshift" in banner
        assert "nemotron-3.5-lightning" in banner.lower()
    else:
        assert "NVIDIA API (direct)" in banner
        assert "2026-10-23 14:59 UTC" in banner
        assert "433 s" in banner or "432 s" in banner
    features = [d.feature for d in session.card.drivers]
    assert features.count("amount_to_income") == 1
    unsupported = [v for v in session.verification if v.status == "UNSUPPORTED"]
    if recorded_live_is_nightshift(session.bundle):
        assert not unsupported
        assert not session.card.rejected
    else:
        assert unsupported


def test_recorded_live_banner_ln1042_nightshift() -> None:
    path = REPLAY_DIR / "LN-1042.live-nightshift-planned.json"
    if not path.is_file():
        pytest.skip("recorded live bundle missing")
    bundle = ReplayBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))
    session = Player(replay_dir=REPLAY_DIR).play_recorded_live("LN-1042")
    banner = recorded_live_banner_text(session.bundle)
    assert banner == recorded_live_banner_text(bundle)


def test_format_recorded_at_minute_truncates_seconds() -> None:
    assert (
        format_recorded_at_minute("2026-10-23T14:59:13.292227+00:00")
        == "2026-10-23 14:59 UTC"
    )


def test_format_original_latency_seconds() -> None:
    assert format_original_latency_seconds(6.758) == "6.8"
    assert format_original_latency_seconds(10.15046) == "10.2"
    assert format_original_latency_seconds(59.94) == "59.9"
    assert format_original_latency_seconds(60.0) == "60"
    assert format_original_latency_seconds(432.7) == "433"


def test_recorded_live_banner_latency_decimal_nightshift() -> None:
    path = REPLAY_DIR / "LN-1301.live-nightshift-planned.json"
    if not path.is_file():
        pytest.skip("recorded live bundle missing")
    bundle = ReplayBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))
    banner = recorded_live_banner_text(bundle)
    assert "original latency 6.8 s" in banner
    path1042 = REPLAY_DIR / "LN-1042.live-nightshift-planned.json"
    if path1042.is_file():
        b1042 = ReplayBundle.model_validate(json.loads(path1042.read_text(encoding="utf-8")))
        assert "original latency 10.2 s" in recorded_live_banner_text(b1042)


def test_bundled_replay_ln1042() -> None:
    path = REPLAY_DIR / "LN-1042.json"
    if not path.is_file():
        pytest.skip("replay bundle missing; run make replay")
    session = Player(replay_dir=REPLAY_DIR).play("LN-1042")
    assert session.card.mode == "replay"
    assert session.card.decision == "decline"
    assert session.card.drivers


@pytest.fixture()
def replay_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    src = REPLAY_DIR
    if not (src / "LN-1042.json").is_file():
        pytest.skip("replay bundle missing; run make replay")
    monkeypatch.setenv("WHYLEDGER_MODE", "replay")
    monkeypatch.setenv("WHYLEDGER_REPLAY_DIR", str(src))
    monkeypatch.delenv("ZETARIS_API_KEY", raising=False)
    monkeypatch.delenv("ZETARIS_LIVE", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("LLM_ROUTE", raising=False)
    _cards.clear()
    return TestClient(create_app())


def test_api_replay_mode_no_data_out(
    replay_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay API must not require gitignored data/out."""
    monkeypatch.setenv("WHYLEDGER_DATA_DIR", "/nonexistent")
    health = replay_client.get("/health")
    assert health.status_code == 200
    assert health.json()["mode"] == "replay"
    assert health.json()["zetaris"]["backend"] == "Replay"

    res = replay_client.post("/ask", json={"loan_id": "LN-1042"})
    assert res.status_code == 200
    body = res.text
    assert "event: replay_info" in body
    info_block = body.split("event: replay_info", 1)[1].split("\n\n", 1)[0]
    info = json.loads(info_block.split("data:", 1)[1].strip())
    assert info["mode"] == "stub_replay"
    assert info["banner"]
    assert "STUB bundles" in info["banner"]
    assert "event: card" in body
    assert '"mode":"replay"' in body.replace(" ", "")
