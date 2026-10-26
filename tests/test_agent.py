"""Investigator agent tests: LN-1042 fixture, verifier trap, read-only guard."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from whyledger.agent import Investigator, MAX_AGENT_STEPS
from whyledger.llm import LLMClient, NvidiaDirectClient, RecordedStubLLM
from whyledger.prompts import MAX_LIVE_LLM_CALLS
from whyledger.zetaris import StubZetarisClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"
FIXTURE = ROOT / "fixtures" / "llm_ln1042.json"
TRUTH = ROOT / "data" / "truth.json"


class _ScriptLLM:
    """Minimal LLM that returns a fixed list of assistant JSON strings."""

    def __init__(self, responses: list[str], label: str = "test-script-llm") -> None:
        self._responses = list(responses)
        self._index = 0
        self.label = label

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        if self._index >= len(self._responses):
            raise IndexError("script LLM exhausted")
        text = self._responses[self._index]
        self._index += 1
        return text


@pytest.fixture(scope="module")
def stub() -> StubZetarisClient:
    if not (OUT / "applications.csv").exists():
        pytest.skip("data/out missing; run data/generate.py")
    return StubZetarisClient(data_dir=OUT)


@pytest.fixture(scope="module")
def truth() -> dict:
    return json.loads(TRUTH.read_text(encoding="utf-8"))


def test_ln1042_fixture_run_matches_truth(stub: StubZetarisClient, truth: dict) -> None:
    llm = RecordedStubLLM(fixture_path=FIXTURE)
    inv = Investigator(
        loan_id="LN-1042",
        client=stub,
        llm=llm,
        mode="stub",
    )
    result = inv.run()
    card = result.card
    assert card is not None
    assert card.mode == "stub"
    assert card.decision == "decline"
    assert len(result.trace) >= 3

    planted = truth["planted"]["LN-1042"]
    top_feature = planted["true_drivers"][0]["feature"]
    assert card.drivers
    assert card.drivers[0].feature == top_feature

    assert all(c.status == "VERIFIED" for c in card.claims)
    assert not any("declared income below 20000" == c.text for c in card.claims)
    assert any(r.text == "declared income below 20000" for r in card.rejected)
    assert card.synthetic is True


def test_malicious_sql_refused_and_traced(stub: StubZetarisClient) -> None:
    malicious = (
        '{"step":"tool","tool":"sql",'
        '"sql":"DELETE FROM applications WHERE loan_id = \'LN-1042\'"}'
    )
    draft = json.dumps(
        {
            "step": "draft_claims",
            "claims": [],
        }
    )
    llm = _ScriptLLM(
        [
            '{"step":"plan","thought":"probe"}',
            malicious,
            draft,
        ]
    )
    inv = Investigator(loan_id="LN-1042", client=stub, llm=llm, mode="stub")
    result = inv.run()
    tool_events = [e for e in result.trace if e.tool == "sql"]
    assert tool_events
    assert "read-only guard refused" in tool_events[0].note
    assert tool_events[0].row_count is None
    assert "DELETE" in (tool_events[0].sql or "")


def _nvidia_llm(contents: list[str]) -> NvidiaDirectClient:
    session = MagicMock()
    posts: list[MagicMock] = []
    for content in contents:
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 1},
        }
        posts.append(resp)
    session.post.side_effect = posts
    return NvidiaDirectClient(api_key="test-key", session=session)


def test_live_llm_invalid_json_traced_not_crashed(
    stub: StubZetarisClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVE_AGENT_MODE", "loop")
    llm = _nvidia_llm(["not-json-at-all"])
    inv = Investigator(loan_id="LN-1042", client=stub, llm=llm, mode="live")
    assert inv._live_llm is True
    assert inv.max_steps == MAX_LIVE_LLM_CALLS
    result = inv.run()
    assert result.card is not None
    refused = [e for e in result.trace if "LLM output refused" in e.note]
    assert refused


def test_live_llm_caps_llm_calls(
    stub: StubZetarisClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVE_AGENT_MODE", "loop")
    tool_only = '{"step":"tool","tool":"sql","sql":"SELECT 1 AS n"}'
    llm = _nvidia_llm([tool_only] * (MAX_LIVE_LLM_CALLS + 5))
    inv = Investigator(loan_id="LN-1042", client=stub, llm=llm, mode="live")
    result = inv.run()
    assert llm.call_count == MAX_LIVE_LLM_CALLS


def test_step_cap_respected(stub: StubZetarisClient) -> None:
    tool_only = '{"step":"tool","tool":"sql","sql":"SELECT 1 AS n"}'
    responses = [tool_only] * (MAX_AGENT_STEPS + 3)
    llm = _ScriptLLM(responses)
    inv = Investigator(
        loan_id="LN-1042",
        client=stub,
        llm=llm,
        mode="stub",
        max_steps=MAX_AGENT_STEPS,
    )
    result = inv.run()
    assert len(result.trace) == MAX_AGENT_STEPS + 1  # scout_intent trace at step 0
    assert llm._index == MAX_AGENT_STEPS
    assert result.card is not None
    assert result.card.claims == []


def test_planned_mode_gathers_evidence_and_drafts(
    stub: StubZetarisClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVE_AGENT_MODE", "planned")
    draft = json.dumps(
        {
            "step": "draft_claims",
            "claims": [
                {
                    "id": "c-outcome",
                    "text": "loan outcome is decline",
                    "values": {"outcome": "decline"},
                    "sql": (
                        "SELECT outcome FROM applications "
                        "WHERE loan_id = 'LN-1042'"
                    ),
                    "evidence_ids": ["ev_application"],
                }
            ],
        }
    )
    llm = _nvidia_llm([draft])
    inv = Investigator(loan_id="LN-1042", client=stub, llm=llm, mode="live")
    assert inv._agent_mode == "planned"
    result = inv.run()
    assert llm.call_count == 1
    planned = [e for e in result.trace if e.tool == "planned_evidence"]
    assert len(planned) == 6
    assert result.card is not None
    assert any(c.text == "loan outcome is decline" for c in result.card.claims)


def test_planned_mode_json_repair_max_two_calls(
    stub: StubZetarisClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVE_AGENT_MODE", "planned")
    llm = _nvidia_llm(["not-json", "still-not-json"])
    inv = Investigator(loan_id="LN-1042", client=stub, llm=llm, mode="live")
    result = inv.run()
    assert llm.call_count == 2
    assert result.card is not None
    assert result.card.claims == []
