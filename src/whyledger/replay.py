"""Record and play back full investigator runs (trace, ledger, WhyCard).

Replay bundles live under ``replay/<loan_id>.json``. They are SYNTHETIC demo
artifacts labelled ``is_synthetic``; Player forces ``mode='replay'`` on cards.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from whyledger.agent import InvestigationResult, TraceEvent
from whyledger.card import (
    CardClaim,
    Driver,
    WhyCard,
    aggregate_drivers_by_feature,
    render_applicant_letter,
)
from whyledger.reason_check import ReasonCheck, check_recorded_reason
from whyledger.ledger import Claim, Evidence, Ledger
from whyledger.verifier import VerificationResult, verify_claims
from whyledger.zetaris import RestZetarisClient, ZetarisClient
from whyledger.zetaris_jdbc import JdbcZetarisClient

_REPLAY_VERSION = 1
RECORDED_LIVE_NIGHTSHIFT_SUFFIX = ".live-nightshift-planned.json"
RECORDED_LIVE_NEMOTRON_SUFFIX = ".live-nemotron-planned.json"
# Legacy alias (NVIDIA-direct recordings).
RECORDED_LIVE_SUFFIX = RECORDED_LIVE_NEMOTRON_SUFFIX

# Fallback latency (ms) for legacy NVIDIA-direct bundles without ``latency_ms``.
_RECORDED_LIVE_LATENCY_MS: dict[str, float] = {
    "LN-1042": 110_550.65,
    "LN-1107": 198_858.1,
    "LN-1230": 432_539.98,
    "LN-1301": 144_796.03,
}


def _zetaris_backend_label(client: ZetarisClient) -> str:
    if isinstance(client, JdbcZetarisClient):
        return "ZETARIS-JDBC"
    if isinstance(client, RestZetarisClient):
        return "Rest"
    return "STUB"
_DEFAULT_REPLAY_DIR = Path(__file__).resolve().parents[2] / "replay"


def default_replay_dir() -> Path:
    """Directory for ``<loan_id>.json`` replay bundles."""
    override = __import__("os").environ.get("WHYLEDGER_REPLAY_DIR", "").strip()
    if override:
        return Path(override)
    return _DEFAULT_REPLAY_DIR


def replay_path(loan_id: str, replay_dir: Path | None = None) -> Path:
    base = replay_dir or default_replay_dir()
    return base / f"{loan_id.upper()}.json"


RecordedLiveVariant = Literal["nightshift", "nvidia_direct"]


def recorded_live_path(
    loan_id: str,
    replay_dir: Path | None = None,
    *,
    variant: RecordedLiveVariant = "nightshift",
) -> Path:
    """Path to a JDBC+LLM planned-mode recording for ``loan_id``."""
    base = replay_dir or default_replay_dir()
    lid = loan_id.upper()
    if variant == "nvidia_direct":
        return base / f"{lid}{RECORDED_LIVE_NEMOTRON_SUFFIX}"
    nightshift = base / f"{lid}{RECORDED_LIVE_NIGHTSHIFT_SUFFIX}"
    if nightshift.is_file():
        return nightshift
    return base / f"{lid}{RECORDED_LIVE_NEMOTRON_SUFFIX}"


def list_recorded_live_loans(replay_dir: Path | None = None) -> list[str]:
    base = replay_dir or default_replay_dir()
    loans: set[str] = set()
    for suffix in (RECORDED_LIVE_NIGHTSHIFT_SUFFIX, RECORDED_LIVE_NEMOTRON_SUFFIX):
        for path in base.glob(f"LN-*{suffix}"):
            loans.add(path.name.split(".", 1)[0].upper())
    return sorted(loans)


def recorded_live_is_nightshift(bundle: ReplayBundle) -> bool:
    """True when the bundle was recorded via Meterless Nightshift."""
    provider = str(bundle.backends.get("llm_provider") or "").strip()
    if provider == "meterless-nightshift":
        return True
    model = str(bundle.backends.get("model") or "")
    return "Meterless Nightshift" in model or "meterless-nightshift" in model


def recorded_live_latency_seconds(bundle: ReplayBundle) -> float:
    """Original run latency in seconds (from bundle or eval table)."""
    raw = getattr(bundle, "latency_ms", None)
    if raw is not None and float(raw) > 0:
        return float(raw) / 1000.0
    lid = bundle.loan_id.upper()
    ms = _RECORDED_LIVE_LATENCY_MS.get(lid)
    if ms is not None:
        return ms / 1000.0
    return 0.0


def format_original_latency_seconds(sec: float) -> str:
    """Replay banner latency: one decimal under 60 s, whole seconds at or above."""
    if sec >= 60:
        return str(int(round(sec)))
    return f"{round(sec, 1):.1f}"


def recorded_live_nightshift_radio_label(
    replay_dir: Path | None = None,
    *,
    sample_loan_id: str = "LN-1042",
) -> str:
    """UI radio label with recorded date from the primary Nightshift bundle."""
    try:
        bundle = Player(replay_dir=replay_dir).load_recorded_live(
            sample_loan_id, variant="nightshift"
        )
        normalized = bundle.recorded_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        day = dt.strftime("%d %b").lstrip("0")
        return f"Nightshift run ({day}, primary)"
    except (FileNotFoundError, ValueError, OSError):
        return "Nightshift run (25 Oct, primary)"


def format_recorded_at_minute(iso_timestamp: str) -> str:
    """UTC timestamp for UI banner, truncated to the minute."""
    normalized = iso_timestamp.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def recorded_live_llm_model_id(bundle: ReplayBundle) -> str:
    """Canonical Nemotron model id for banners and provenance (not Nightshift wrapper text)."""
    llm = str(bundle.backends.get("llm_model") or "").strip()
    if llm:
        return llm
    raw = str(bundle.backends.get("model") or "").strip()
    if "(" in raw and ")" in raw:
        inner = raw[raw.index("(") + 1 : raw.rindex(")")]
        if "/" in inner:
            return inner.strip()
    if raw:
        return raw
    return "nvidia/nemotron-3.5-lightning-30b-a3b"


def recorded_live_banner_text(bundle: ReplayBundle) -> str:
    """Human-readable replay banner for the UI."""
    ts = format_recorded_at_minute(bundle.recorded_at)
    sec = recorded_live_latency_seconds(bundle)
    lat_sec = format_original_latency_seconds(sec)
    llm_model = recorded_live_llm_model_id(bundle)
    if recorded_live_is_nightshift(bundle):
        stack = f"Zetaris live (JDBC) + {llm_model} via Meterless Nightshift"
    else:
        stack = f"Zetaris live (JDBC) + {llm_model} via NVIDIA API (direct)"
    return (
        f"Replay of a recorded live run · {stack} · "
        f"recorded {ts} · original latency {lat_sec} s"
    )


class ReplayBundle(BaseModel):
    """Serialized full run for offline Replay mode."""

    model_config = ConfigDict(extra="forbid")

    version: int = _REPLAY_VERSION
    loan_id: str
    is_synthetic: Literal[True] = True
    recorded_at: str
    backends: dict[str, str] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    card: dict[str, Any] = Field(default_factory=dict)
    verification: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float | None = None


@dataclass
class ReplaySession:
    """Materialised replay ready for SSE and evidence APIs."""

    loan_id: str
    trace: list[TraceEvent]
    card: WhyCard
    ledger: Ledger
    verification: list[VerificationResult]
    bundle: ReplayBundle
    replay_source: Literal["stub", "recorded_live"] = "stub"


def _trace_to_dict(event: TraceEvent) -> dict[str, Any]:
    out: dict[str, Any] = {
        "step": event.step,
        "tool": event.tool,
        "sql": event.sql,
        "row_count": event.row_count,
        "note": event.note,
        "intent": event.intent,
        "routing_hash": event.routing_hash,
    }
    if event.provider:
        out["provider"] = event.provider
    if event.model:
        out["model"] = event.model
    return out


def _trace_from_dict(raw: dict[str, Any]) -> TraceEvent:
    return TraceEvent(
        step=int(raw.get("step", 0)),
        tool=raw.get("tool"),
        sql=raw.get("sql"),
        row_count=raw.get("row_count"),
        note=str(raw.get("note", "")),
        intent=raw.get("intent"),
        routing_hash=raw.get("routing_hash"),
        provider=raw.get("provider"),
        model=raw.get("model"),
    )


def _claim_to_dict(claim: Claim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "text": claim.text,
        "values": dict(claim.values),
        "query_ids": list(claim.query_ids),
    }


def _claim_from_dict(raw: dict[str, Any]) -> Claim:
    values_raw = raw.get("values") or {}
    if not isinstance(values_raw, dict):
        values_raw = {}
    values: dict[str, int | float | str] = {}
    for k, v in values_raw.items():
        if isinstance(v, (int, float, str)) and not isinstance(v, bool):
            values[str(k)] = v
        else:
            values[str(k)] = str(v)
    qids = raw.get("query_ids") or []
    return Claim(
        id=str(raw["id"]),
        text=str(raw.get("text", "")),
        values=values,
        query_ids=[str(q) for q in qids],
    )


def _evidence_to_dict(ev: Evidence) -> dict[str, Any]:
    return {
        "query_id": ev.query_id,
        "sql": ev.sql,
        "columns": list(ev.columns),
        "rows": [dict(r) for r in ev.rows],
        "sha256": ev.sha256,
    }


def _verification_to_dict(v: VerificationResult) -> dict[str, Any]:
    if is_dataclass(v):
        return asdict(v)
    return {
        "claim_id": v.claim_id,
        "status": v.status,
        "reason": v.reason,
    }


def refresh_stored_reason_check_copy(
    bundle: ReplayBundle,
    client: ZetarisClient,
) -> ReasonCheck:
    """Recompute reason_check headline/detail offline; status must match the bundle."""
    card_raw = bundle.card
    reason_raw = card_raw.get("reason_check")
    if not reason_raw:
        raise ValueError(f"{bundle.loan_id}: card has no reason_check")
    stored = ReasonCheck.model_validate(reason_raw)
    fresh = check_recorded_reason(
        client, bundle.loan_id, ledger=ledger_from_bundle(bundle)
    )
    if fresh.status != stored.status:
        raise ValueError(
            f"{bundle.loan_id}: reason_check status {stored.status!r} "
            f"!= recomputed {fresh.status!r}"
        )
    return stored.model_copy(update={"detail": fresh.detail, "headline": fresh.headline})


def rebuild_applicant_letter(bundle: ReplayBundle) -> str:
    """Re-render principal-reasons letter from stored card fields and evidence."""
    card_raw = bundle.card
    reason_raw = card_raw.get("reason_check")
    reason = ReasonCheck.model_validate(reason_raw) if reason_raw else None
    drivers = [Driver.model_validate(d) for d in card_raw.get("drivers") or []]
    claims = [CardClaim.model_validate(c) for c in card_raw.get("claims") or []]
    return render_applicant_letter(
        loan_id=bundle.loan_id,
        decision=str(card_raw.get("decision", "")),
        drivers=drivers,
        claims=claims,
        reason_check=reason,
        ledger=ledger_from_bundle(bundle),
    )


def ledger_from_bundle(bundle: ReplayBundle) -> Ledger:
    """Rebuild append-only ledger from a replay bundle."""
    ledger = Ledger()
    for item in bundle.evidence:
        ledger.append_evidence(
            query_id=str(item["query_id"]),
            sql=str(item["sql"]),
            columns=[str(c) for c in item.get("columns", [])],
            rows=item.get("rows") or [],
            sha256=str(item.get("sha256", "")),
        )
    for item in bundle.claims:
        ledger.append_claim(_claim_from_dict(item))
    return ledger


def verification_from_bundle(bundle: ReplayBundle) -> list[VerificationResult]:
    out: list[VerificationResult] = []
    for item in bundle.verification:
        out.append(
            VerificationResult(
                claim_id=str(item["claim_id"]),
                status=str(item["status"]),
                reason=str(item.get("reason", "")),
            )
        )
    return out


def card_without_mode(card: WhyCard) -> dict[str, Any]:
    """Card payload for equality checks (ignore ``mode`` and timestamps)."""
    data = card.model_dump()
    data.pop("mode", None)
    data.pop("verified_at", None)
    return data


def cards_equal_except_mode(recorded: WhyCard, replayed: WhyCard) -> bool:
    return card_without_mode(recorded) == card_without_mode(replayed)


class Recorder:
    """Capture a live/stub investigator run to ``replay/<loan_id>.json``."""

    def __init__(self, replay_dir: Path | None = None) -> None:
        self.replay_dir = replay_dir or default_replay_dir()

    def build_bundle(
        self,
        *,
        result: InvestigationResult,
        ledger: Ledger,
        verification: list[VerificationResult],
        zetaris: ZetarisClient,
        model_label: str,
    ) -> ReplayBundle:
        if result.card is None:
            raise ValueError("cannot record a run without a WhyCard")
        evidence = [_evidence_to_dict(ev) for ev in ledger.evidence_map.values()]
        return ReplayBundle(
            loan_id=result.loan_id,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            backends={
                "zetaris": _zetaris_backend_label(zetaris),
                "model": model_label,
            },
            trace=[_trace_to_dict(ev) for ev in result.trace],
            claims=[_claim_to_dict(c) for c in ledger.claims],
            evidence=evidence,
            card=result.card.model_dump(),
            verification=[_verification_to_dict(v) for v in verification],
        )

    def save(self, bundle: ReplayBundle, *, loan_id: str | None = None) -> Path:
        lid = (loan_id or bundle.loan_id).upper()
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        path = replay_path(lid, self.replay_dir)
        path.write_text(
            json.dumps(bundle.model_dump(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def record_run(
        self,
        *,
        result: InvestigationResult,
        ledger: Ledger,
        verification: list[VerificationResult],
        zetaris: ZetarisClient,
        model_label: str,
    ) -> Path:
        bundle = self.build_bundle(
            result=result,
            ledger=ledger,
            verification=verification,
            zetaris=zetaris,
            model_label=model_label,
        )
        return self.save(bundle)


class Player:
    """Serve a recorded bundle with ``mode='replay'`` (no Zetaris / LLM)."""

    def __init__(self, replay_dir: Path | None = None) -> None:
        self.replay_dir = replay_dir or default_replay_dir()

    def load(self, loan_id: str) -> ReplayBundle:
        path = replay_path(loan_id, self.replay_dir)
        if not path.is_file():
            raise FileNotFoundError(f"no replay bundle for {loan_id.upper()}: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ReplayBundle.model_validate(raw)

    def load_recorded_live(
        self,
        loan_id: str,
        *,
        variant: RecordedLiveVariant = "nightshift",
    ) -> ReplayBundle:
        path = recorded_live_path(loan_id, self.replay_dir, variant=variant)
        if not path.is_file():
            nemotron = (
                self.replay_dir
                / f"{loan_id.upper()}{RECORDED_LIVE_NEMOTRON_SUFFIX}"
            )
            nightshift = (
                self.replay_dir
                / f"{loan_id.upper()}{RECORDED_LIVE_NIGHTSHIFT_SUFFIX}"
            )
            raise FileNotFoundError(
                f"no recorded live bundle for {loan_id.upper()}: "
                f"tried {nightshift.name} and {nemotron.name}"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ReplayBundle.model_validate(raw)

    def play(self, loan_id: str) -> ReplaySession:
        bundle = self.load(loan_id)
        trace = [_trace_from_dict(item) for item in bundle.trace]
        recorded_card = WhyCard.model_validate(bundle.card)
        card_data = recorded_card.model_dump()
        card_data["mode"] = "replay"
        card_data["drivers"] = [
            d.model_dump()
            for d in aggregate_drivers_by_feature(recorded_card.drivers)
        ]
        card = WhyCard.model_validate(card_data)
        ledger = ledger_from_bundle(bundle)
        verification = verification_from_bundle(bundle)
        return ReplaySession(
            loan_id=bundle.loan_id.upper(),
            trace=trace,
            card=card,
            ledger=ledger,
            verification=verification,
            bundle=bundle,
            replay_source="stub",
        )

    def play_recorded_live(
        self,
        loan_id: str,
        *,
        variant: RecordedLiveVariant = "nightshift",
    ) -> ReplaySession:
        bundle = self.load_recorded_live(loan_id, variant=variant)
        trace = [_trace_from_dict(item) for item in bundle.trace]
        recorded_card = WhyCard.model_validate(bundle.card)
        card_data = recorded_card.model_dump()
        card_data["mode"] = "replay"
        card_data["drivers"] = [
            d.model_dump()
            for d in aggregate_drivers_by_feature(recorded_card.drivers)
        ]
        card = WhyCard.model_validate(card_data)
        ledger = ledger_from_bundle(bundle)
        verification = verification_from_bundle(bundle)
        return ReplaySession(
            loan_id=bundle.loan_id.upper(),
            trace=trace,
            card=card,
            ledger=ledger,
            verification=verification,
            bundle=bundle,
            replay_source="recorded_live",
        )


def record_investigator_run(
    inv: Any,
    *,
    result: InvestigationResult,
    client: ZetarisClient,
    recorder: Recorder | None = None,
) -> Path:
    """Helper: verify claims and write replay JSON for ``inv``'s last run."""
    verification = verify_claims(
        inv.ledger.claims,
        inv.ledger,
        client,
        subject_loan_id=inv.loan_id,
    )
    rec = recorder or Recorder()
    return rec.record_run(
        result=result,
        ledger=inv.ledger,
        verification=verification,
        zetaris=client,
        model_label=inv.llm.label,
    )
