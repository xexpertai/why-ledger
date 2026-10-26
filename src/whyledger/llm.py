"""OpenAI-compatible LLM client and STUB recorded Nemotron fixture replay.

``METERLESS_*`` (Nightshift Runtime), ``NVIDIA_API_KEY`` / ``MODEL_API_KEY``,
``MODEL_BASE_URL``, and ``NEMOTRON_MODEL`` come from the environment. Secrets
are never logged or included in repr.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

import requests

_DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "llm_ln1042.json"
)
_DEFAULT_METERLESS_BASE = "http://127.0.0.1:8088/v1"
_NVIDIA_INTEGRATE_BASE = "https://integrate.api.nvidia.com/v1"
_DEFAULT_NEMOTRON_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
METERLESS_PROVIDER_ID = "meterless-nightshift"
NVIDIA_PROVIDER_ID = "nvidia-direct"

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 5
_NVIDIA_MAX_ATTEMPTS = 3  # initial try + 2 rate-limit retries
_INITIAL_BACKOFF_S = 1.0
_DEFAULT_NVIDIA_HTTP_TIMEOUT_S = 180.0


def _nvidia_http_timeout_s() -> float:
    raw = os.environ.get("NEMOTRON_HTTP_TIMEOUT", "").strip()
    if raw:
        return float(raw)
    return _DEFAULT_NVIDIA_HTTP_TIMEOUT_S


@runtime_checkable
class LLMClient(Protocol):
    """Minimal chat interface used by the agent loop."""

    @property
    def label(self) -> str:
        """Human-readable backend name for cards and health checks."""
        ...

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        """Run one chat completion; returns assistant message text."""
        ...


@dataclass(frozen=True)
class MeterlessCallLogEntry:
    """Per-completion audit row (model id from the Runtime response, never secrets)."""

    model: str
    call_index: int


@dataclass(frozen=True)
class NvidiaCallLogEntry:
    """Per-completion audit row for NVIDIA integrate API (response model field)."""

    model: str
    call_index: int


def extract_json_object(raw: str) -> dict[str, Any]:
    """Parse one JSON object from model text (strict or fenced / substring)."""
    text = raw.strip()
    if not text:
        raise ValueError("LLM response is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence:
            payload = json.loads(fence.group(1))
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("LLM response is not valid JSON") from None
            payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON must be an object")
    return payload


def llm_backend_fields(client: LLMClient) -> dict[str, str]:
    """Backends block for replay bundles (provider + response model, no secrets)."""
    provider, model = llm_trace_meta(client)
    fields: dict[str, str] = {"model": client.label}
    if provider:
        fields["llm_provider"] = provider
    if model:
        fields["llm_model"] = model
    return fields


def llm_trace_meta(client: LLMClient) -> tuple[str | None, str | None]:
    """Provider id and response model id for investigator trace rows (no secrets)."""
    if isinstance(client, MeterlessRuntimeClient):
        model = client.call_log[-1].model if client.call_log else client.active_model_id
        return METERLESS_PROVIDER_ID, model
    if isinstance(client, NvidiaDirectClient):
        model = client.call_log[-1].model if client.call_log else client.active_model_id
        return NVIDIA_PROVIDER_ID, model
    return None, None


def is_live_llm(client: LLMClient) -> bool:
    """True for configured HTTP backends (not STUB fixture or test doubles)."""
    return isinstance(
        client,
        (OpenAILikeClient, MeterlessRuntimeClient, NvidiaDirectClient),
    )


def _nvidia_api_key() -> str:
    return (
        os.environ.get("NVIDIA_API_KEY", "").strip()
        or os.environ.get("MODEL_API_KEY", "").strip()
    )


def nvidia_direct_configured() -> bool:
    """True when NVIDIA direct credentials are present in the environment."""
    return bool(_nvidia_api_key())


class OpenAILikeClient:
    """Live Nemotron / OpenAI-compatible chat completions via HTTP POST."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
        initial_backoff_s: float = _INITIAL_BACKOFF_S,
    ) -> None:
        self._base_url = (base_url or os.environ.get("MODEL_BASE_URL", "")).rstrip("/")
        self._api_key = api_key if api_key is not None else os.environ.get("MODEL_API_KEY", "")
        self._model = model or os.environ.get(
            "NEMOTRON_MODEL", "nvidia/nemotron-3-super-120b-a12b"
        )
        self._session = session or requests.Session()
        self._sleep = sleep if sleep is not None else _time_sleep
        self._max_attempts = max_attempts
        self._initial_backoff_s = initial_backoff_s
        self.call_count = 0
        self.token_count = 0
        if not self._base_url:
            raise ValueError("MODEL_BASE_URL is required")
        if not self._api_key:
            raise ValueError("MODEL_API_KEY is required")

    @property
    def label(self) -> str:
        return self._model

    def __repr__(self) -> str:
        return (
            f"OpenAILikeClient(base_url={self._base_url!r}, "
            f"model={self._model!r}, api_key=***)"
        )

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        backoff = self._initial_backoff_s
        last_exc: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                resp = self._session.post(url, headers=headers, json=payload, timeout=120)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 >= self._max_attempts:
                    raise
                self._sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                if attempt + 1 >= self._max_attempts:
                    resp.raise_for_status()
                self._sleep(backoff)
                backoff *= 2
                continue

            resp.raise_for_status()
            self.call_count += 1
            body = resp.json()
            usage = body.get("usage") or {}
            total = usage.get("total_tokens")
            if isinstance(total, int):
                self.token_count += total
            elif isinstance(total, (float, str)):
                try:
                    self.token_count += int(total)
                except (TypeError, ValueError):
                    pass
            return _extract_assistant_content(body)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("chat failed after retries")


class NvidiaDirectClient:
    """NVIDIA integrate.api.nvidia.com OpenAI-compatible chat (free-tier backoff)."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = _NVIDIA_MAX_ATTEMPTS,
        initial_backoff_s: float = _INITIAL_BACKOFF_S,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("MODEL_BASE_URL") or _NVIDIA_INTEGRATE_BASE
        ).rstrip("/")
        self._api_key = api_key if api_key is not None else _nvidia_api_key()
        self._model = model or os.environ.get("NEMOTRON_MODEL", _DEFAULT_NEMOTRON_MODEL)
        self._session = session or requests.Session()
        self._sleep = sleep if sleep is not None else _time_sleep
        self._max_attempts = max_attempts
        self._initial_backoff_s = initial_backoff_s
        self.call_count = 0
        self.token_count = 0
        self.call_log: list[NvidiaCallLogEntry] = []
        if not self._api_key:
            raise ValueError("NVIDIA_API_KEY is required")

    @property
    def active_model_id(self) -> str:
        return self._model

    @property
    def provider_id(self) -> str:
        return NVIDIA_PROVIDER_ID

    @property
    def label(self) -> str:
        return f"Nemotron via integrate.api.nvidia.com ({self._model})"

    def __repr__(self) -> str:
        return (
            f"NvidiaDirectClient(base_url={self._base_url!r}, "
            f"model={self._model!r}, api_key=***)"
        )

    def test_connection(self) -> tuple[bool, str]:
        """Probe with a one-line chat; returns (ok, model name from last response)."""
        try:
            text = self.chat(
                [{"role": "user", "content": "Reply with the word OK."}],
                json_mode=False,
            )
            model = self.call_log[-1].model if self.call_log else self._model
            ok = bool(text.strip()) and bool(model)
            return ok, model
        except Exception:
            return False, self._model

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        backoff = self._initial_backoff_s
        last_exc: Exception | None = None
        last_status: int | None = None
        for attempt in range(self._max_attempts):
            try:
                resp = self._session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=_nvidia_http_timeout_s(),
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 >= self._max_attempts:
                    raise
                self._sleep(backoff)
                backoff *= 2
                continue

            last_status = resp.status_code
            if resp.status_code in _RETRYABLE_STATUS:
                if attempt + 1 >= self._max_attempts:
                    if resp.status_code == 429:
                        raise RuntimeError(
                            "NVIDIA API rate limit exceeded after retries"
                        ) from None
                    resp.raise_for_status()
                self._sleep(backoff)
                backoff *= 2
                continue

            resp.raise_for_status()
            self.call_count += 1
            body = resp.json()
            response_model = str(body.get("model") or self._model)
            self.call_log.append(
                NvidiaCallLogEntry(model=response_model, call_index=self.call_count)
            )
            usage = body.get("usage") or {}
            total = usage.get("total_tokens")
            if isinstance(total, int):
                self.token_count += total
            return _extract_assistant_content(body)

        if last_status == 429:
            raise RuntimeError("NVIDIA API rate limit exceeded after retries")
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("NVIDIA chat failed after retries")


class MeterlessRuntimeClient:
    """Meterless Nightshift Runtime: OpenAI-compatible /v1/chat/completions."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        runtime_token: str | None = None,
        model: str | None = None,
        app_id: str = "why-ledger",
        session: requests.Session | None = None,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
        initial_backoff_s: float = _INITIAL_BACKOFF_S,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("METERLESS_BASE_URL") or _DEFAULT_METERLESS_BASE
        ).rstrip("/")
        self._token = runtime_token or os.environ.get("METERLESS_RUNTIME_TOKEN", "")
        self._requested_model = model or os.environ.get("METERLESS_MODEL", "meterless/auto")
        self._app_id = app_id
        self._session = session or requests.Session()
        self._sleep = sleep if sleep is not None else _time_sleep
        self._max_attempts = max_attempts
        self._initial_backoff_s = initial_backoff_s
        self._resolved_model: str | None = None
        self.call_count = 0
        self.token_count = 0
        self.call_log: list[MeterlessCallLogEntry] = []
        if not self._token:
            raise ValueError("METERLESS_RUNTIME_TOKEN is required")

    @property
    def active_model_id(self) -> str:
        return self._active_model()

    @property
    def provider_id(self) -> str:
        return METERLESS_PROVIDER_ID

    @property
    def label(self) -> str:
        mid = self._resolved_model or self._requested_model
        return f"Meterless Nightshift ({mid})"

    def __repr__(self) -> str:
        return (
            f"MeterlessRuntimeClient(base_url={self._base_url!r}, "
            f"model={self.label!r}, api_key=***)"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "meterless-app": self._app_id,
            "meterless-scope": "user",
        }

    def list_model_ids(self) -> list[str]:
        """GET /models — model ids exposed by Nightshift Runtime (no secrets)."""
        url = f"{self._base_url}/models"
        resp = self._session.get(url, headers=self._headers(), timeout=60)
        resp.raise_for_status()
        body = resp.json()
        models = body.get("data") if isinstance(body, dict) else None
        if not isinstance(models, list):
            raise ValueError("unexpected Meterless /models response")
        ids: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or item.get("name") or "").strip()
            if mid:
                ids.append(mid)
        return ids

    def discover_model(self) -> str:
        """GET /models and pick a route (``nvidia/*`` or ``meterless/*``, prefer Nemotron)."""
        candidates: list[str] = []
        for mid in self.list_model_ids():
            if mid.startswith("meterless/") or mid.startswith("nvidia/"):
                candidates.append(mid)

        if not candidates:
            return self._requested_model

        if self._requested_model in candidates:
            return self._requested_model

        lightning = [
            m
            for m in candidates
            if "nemotron" in m.lower() and "lightning" in m.lower()
        ]
        if lightning:
            return sorted(lightning)[0]
        nemotron = [m for m in candidates if "nemotron" in m.lower()]
        if nemotron:
            return sorted(nemotron)[0]
        return sorted(candidates)[0]

    def _active_model(self) -> str:
        if self._resolved_model is None:
            if self._requested_model == "meterless/auto" or self._requested_model.startswith(
                "meterless/"
            ):
                self._resolved_model = self.discover_model()
            else:
                self._resolved_model = self._requested_model
        return self._resolved_model

    def probe_nightshift(self) -> tuple[bool, str, int]:
        """List ``nvidia/*`` models and run one tiny chat; returns (ok, model, nvidia_count)."""
        try:
            nvidia_count = sum(1 for mid in self.list_model_ids() if mid.startswith("nvidia/"))
            text = self.chat(
                [{"role": "user", "content": "Reply with the word OK."}],
                json_mode=False,
            )
            model = self.call_log[-1].model if self.call_log else self.active_model_id
            ok = bool(text.strip()) and bool(model)
            return ok, model, nvidia_count
        except Exception:
            return False, self.active_model_id, 0

    def test_connection(self) -> tuple[bool, str]:
        """Probe Runtime with a one-line chat; returns (ok, model_name_from_response)."""
        ok, model, _ = self.probe_nightshift()
        return ok, model

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        url = f"{self._base_url}/chat/completions"
        model = self._active_model()
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        backoff = self._initial_backoff_s
        last_exc: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                resp = self._session.post(
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=_nvidia_http_timeout_s(),
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 >= self._max_attempts:
                    raise
                self._sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                if attempt + 1 >= self._max_attempts:
                    resp.raise_for_status()
                self._sleep(backoff)
                backoff *= 2
                continue

            resp.raise_for_status()
            self.call_count += 1
            body = resp.json()
            response_model = str(body.get("model") or model)
            self.call_log.append(
                MeterlessCallLogEntry(model=response_model, call_index=self.call_count)
            )
            usage = body.get("usage") or {}
            total = usage.get("total_tokens")
            if isinstance(total, int):
                self.token_count += total
            return _extract_assistant_content(body)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("chat failed after retries")


def _extract_assistant_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("unexpected LLM response: missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("unexpected LLM response: missing message")
    content = message.get("content")
    if content is None:
        return ""
    return str(content)


def _time_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


class RecordedStubLLM:
    """STUB — replay scripted Nemotron replies from a JSON fixture (no network).

    STUB: for Replay mode and offline tests only. Not a live model.
    """

    name = "STUB-recorded-nemotron"

    def __init__(self, fixture_path: str | Path | None = None) -> None:
        path = Path(fixture_path) if fixture_path else _DEFAULT_FIXTURE
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._loan_id = str(raw.get("loan_id", ""))
        responses = raw.get("responses")
        if not isinstance(responses, list) or not responses:
            raise ValueError(f"STUB fixture must contain a non-empty responses list: {path}")
        self._responses = [str(r) for r in responses]
        self._index = 0
        self.call_count = 0
        self.token_count = 0

    @property
    def label(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return (
            f"RecordedStubLLM(name={self.name!r}, loan_id={self._loan_id!r}, "
            f"responses={len(self._responses)}, index={self._index})"
        )

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        """STUB: return the next scripted assistant string from the fixture."""
        if self._index >= len(self._responses):
            raise IndexError("STUB fixture exhausted: no more scripted responses")
        self.call_count += 1
        # STUB token estimate for eval counters only.
        self.token_count += sum(len(str(m.get("content", ""))) for m in messages) // 4
        text = self._responses[self._index]
        self._index += 1
        return text


def get_llm() -> LLMClient:
    """Route: ``LLM_ROUTE`` override, else Meterless, else NVIDIA direct, else STUB."""
    route = os.environ.get("LLM_ROUTE", "").strip().lower()
    if route == "nvidia":
        return NvidiaDirectClient()
    if route == "meterless":
        return MeterlessRuntimeClient()
    if route == "stub":
        return RecordedStubLLM()
    if os.environ.get("METERLESS_RUNTIME_TOKEN", "").strip():
        return MeterlessRuntimeClient()
    if _nvidia_api_key():
        return NvidiaDirectClient()
    return RecordedStubLLM()
