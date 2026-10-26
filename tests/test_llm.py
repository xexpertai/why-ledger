"""Tests for LLM client: mocked HTTP, STUB fixture, factory selection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from whyledger.llm import (
    MeterlessRuntimeClient,
    NvidiaDirectClient,
    OpenAILikeClient,
    RecordedStubLLM,
    extract_json_object,
    get_llm,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "llm_ln1042.json"


def _ok_response(
    content: str = "hello",
    total_tokens: int = 42,
    model: str = "test-model",
) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "model": model,
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": total_tokens},
    }
    return mock


def test_openai_retries_on_429_with_backoff() -> None:
    sleeps: list[float] = []
    session = MagicMock()
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.raise_for_status = MagicMock()
    session.post.side_effect = [rate_limited, _ok_response("ok", 10)]

    client = OpenAILikeClient(
        base_url="https://api.test/v1",
        api_key="secret-llm-key",
        model="test-model",
        session=session,
        sleep=sleeps.append,
        initial_backoff_s=0.5,
    )
    out = client.chat([{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert session.post.call_count == 2
    assert sleeps == [0.5]
    assert client.call_count == 1
    assert client.token_count == 10


def test_openai_counters_on_success() -> None:
    session = MagicMock()
    session.post.return_value = _ok_response("x", 100)
    client = OpenAILikeClient(
        base_url="https://api.test/v1",
        api_key="k",
        session=session,
    )
    client.chat([{"role": "user", "content": "a"}])
    client.chat([{"role": "user", "content": "b"}], json_mode=True)
    assert client.call_count == 2
    assert client.token_count == 200
    body = session.post.call_args.kwargs["json"]
    assert body["response_format"] == {"type": "json_object"}


def test_openai_repr_hides_api_key() -> None:
    client = OpenAILikeClient(
        base_url="https://api.test/v1",
        api_key="super-secret-model-key",
    )
    rep = repr(client)
    assert "super-secret-model-key" not in rep
    assert "api_key=***" in rep


def test_get_llm_factory_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_ROUTE", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    stub = get_llm()
    assert isinstance(stub, RecordedStubLLM)
    assert stub.label == "STUB-recorded-nemotron"

    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "live-key")
    live = get_llm()
    assert isinstance(live, NvidiaDirectClient)
    assert "integrate.api.nvidia.com" in live.label
    assert "nemotron-3.5-lightning-30b-a3b" in live.label


def test_get_llm_meterless_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_ROUTE", raising=False)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "runtime-token")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-key")
    monkeypatch.setenv("METERLESS_BASE_URL", "http://meterless.test/v1")
    client = get_llm()
    assert isinstance(client, MeterlessRuntimeClient)
    assert "runtime-token" not in repr(client)


def test_get_llm_route_nvidia_overrides_meterless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTE", "nvidia")
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "runtime-token")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-key")
    client = get_llm()
    assert isinstance(client, NvidiaDirectClient)


def test_nvidia_retries_on_429_max_two_retries() -> None:
    sleeps: list[float] = []
    session = MagicMock()
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.raise_for_status = MagicMock()
    session.post.side_effect = [
        rate_limited,
        rate_limited,
        _ok_response("ok", 7),
    ]
    client = NvidiaDirectClient(
        api_key="nv-key",
        session=session,
        sleep=sleeps.append,
        initial_backoff_s=0.25,
    )
    out = client.chat([{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert session.post.call_count == 3
    assert sleeps == [0.25, 0.5]
    assert client.call_log[-1].model == "test-model"


def test_nvidia_rate_limit_clear_error_after_retries() -> None:
    session = MagicMock()
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.raise_for_status = MagicMock()
    session.post.return_value = rate_limited
    client = NvidiaDirectClient(
        api_key="nv-key",
        session=session,
        sleep=lambda _s: None,
        max_attempts=3,
    )
    with pytest.raises(RuntimeError, match="rate limit"):
        client.chat([{"role": "user", "content": "hi"}])


def test_nvidia_chat_logs_response_model() -> None:
    session = MagicMock()
    chat_resp = MagicMock()
    chat_resp.status_code = 200
    chat_resp.raise_for_status = MagicMock()
    chat_resp.json.return_value = {
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "choices": [{"message": {"content": '{"step":"plan","thought":"x"}'}}],
        "usage": {"total_tokens": 3},
    }
    session.post.return_value = chat_resp
    client = NvidiaDirectClient(api_key="k", session=session)
    out = client.chat([{"role": "user", "content": "hi"}], json_mode=True)
    assert "plan" in out
    assert client.call_log[-1].model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    body = session.post.call_args.kwargs["json"]
    assert body["response_format"] == {"type": "json_object"}


def test_extract_json_object_fenced() -> None:
    raw = 'Here you go:\n```json\n{"step": "plan", "thought": "t"}\n```'
    payload = extract_json_object(raw)
    assert payload["step"] == "plan"


def test_meterless_discover_prefers_nvidia_nemotron() -> None:
    session = MagicMock()
    models_resp = MagicMock()
    models_resp.raise_for_status = MagicMock()
    models_resp.json.return_value = {
        "data": [
            {"id": "nvidia/other"},
            {"id": "nvidia/nemotron-3.5-lightning-30b-a3b"},
        ]
    }
    session.get.return_value = models_resp
    client = MeterlessRuntimeClient(
        base_url="http://meterless.test/v1",
        runtime_token="t",
        model="meterless/auto",
        session=session,
    )
    assert client.discover_model() == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_meterless_discover_prefers_nemotron() -> None:
    session = MagicMock()
    models_resp = MagicMock()
    models_resp.raise_for_status = MagicMock()
    models_resp.json.return_value = {
        "data": [
            {"id": "meterless/other"},
            {"id": "meterless/nemotron-route"},
        ]
    }
    session.get.return_value = models_resp
    client = MeterlessRuntimeClient(
        base_url="http://meterless.test/v1",
        runtime_token="t",
        session=session,
    )
    assert client.discover_model() == "meterless/nemotron-route"


def test_meterless_uses_explicit_meterless_model_env() -> None:
    session = MagicMock()
    chat_resp = MagicMock()
    chat_resp.status_code = 200
    chat_resp.raise_for_status = MagicMock()
    chat_resp.json.return_value = {
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "choices": [{"message": {"content": "OK"}}],
    }
    session.post.return_value = chat_resp
    client = MeterlessRuntimeClient(
        base_url="http://meterless.test/v1",
        runtime_token="t",
        model="nvidia/nemotron-3.5-lightning-30b-a3b",
        session=session,
    )
    client.chat([{"role": "user", "content": "hi"}])
    assert session.get.call_count == 0
    body = session.post.call_args.kwargs["json"]
    assert body["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_meterless_chat_logs_response_model() -> None:
    session = MagicMock()
    models_resp = MagicMock()
    models_resp.raise_for_status = MagicMock()
    models_resp.json.return_value = {"data": [{"id": "meterless/fixed"}]}
    chat_resp = MagicMock()
    chat_resp.status_code = 200
    chat_resp.raise_for_status = MagicMock()
    chat_resp.json.return_value = {
        "model": "meterless/nemotron-70b",
        "choices": [{"message": {"content": "OK"}}],
        "usage": {"total_tokens": 5},
    }
    session.get.return_value = models_resp
    session.post.return_value = chat_resp
    client = MeterlessRuntimeClient(
        base_url="http://meterless.test/v1",
        runtime_token="t",
        model="meterless/auto",
        session=session,
    )
    out = client.chat([{"role": "user", "content": "hi"}])
    assert out == "OK"
    assert client.call_log[-1].model == "meterless/nemotron-70b"
    headers = session.post.call_args.kwargs["headers"]
    assert headers["meterless-app"] == "why-ledger"
    assert headers["meterless-scope"] == "user"
    assert headers["Authorization"] == "Bearer t"


def test_recorded_stub_ln1042_draft_claims() -> None:
    stub = RecordedStubLLM(fixture_path=FIXTURE)
    assert "STUB" in RecordedStubLLM.__doc__ or ""
    assert stub.label == "STUB-recorded-nemotron"
    # Advance through plan + tool steps to the draft_claims response.
    for _ in range(5):
        stub.chat([{"role": "user", "content": "continue"}])
    draft_raw = stub.chat([{"role": "user", "content": "draft claims"}])
    draft = json.loads(draft_raw)
    assert draft["step"] == "draft_claims"
    claims = draft["claims"]
    assert len(claims) == 3
    by_id = {c["id"]: c for c in claims}
    assert by_id["c-income-lie"]["text"] == "declared income below 20000"
    assert by_id["c-late"]["values"]["late_count"] == 3
    assert by_id["c-util"]["values"]["utilisation"] == 91
    for c in claims:
        assert c.get("sql")
        assert c.get("text")
        assert isinstance(c.get("values"), dict)
