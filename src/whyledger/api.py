"""FastAPI surface: health, SSE /ask, card and evidence retrieval, static UI."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from whyledger.agent import Investigator, TraceEvent
from whyledger.card import Mode, WhyCard, enrich_why_card
from whyledger.ledger import Ledger
from whyledger.llm import LLMClient, get_llm
from whyledger.replay import (
    Player,
    ReplaySession,
    default_replay_dir,
    list_recorded_live_loans,
    recorded_live_banner_text,
    recorded_live_nightshift_radio_label,
)
from whyledger.verifier import VerificationResult, verify_claims
from whyledger.verifier_stress import load_stress_json
from whyledger.zetaris import RestZetarisClient, StubZetarisClient, ZetarisClient
from whyledger.zetaris_jdbc import JdbcZetarisClient

_UI_DIR = Path(__file__).resolve().parent / "ui"


class AskBody(BaseModel):
    """POST /ask payload."""

    model_config = ConfigDict(extra="forbid")

    question: str | None = None
    loan_id: str | None = None


class RecordedLiveReplayBody(BaseModel):
    """POST /replay/recorded-live payload."""

    model_config = ConfigDict(extra="forbid")

    loan_id: str
    variant: Literal["nightshift", "nvidia_direct"] = "nightshift"


@dataclass
class _CardRecord:
    card: WhyCard
    ledger: Ledger
    verification: list[VerificationResult]


_cards: dict[str, _CardRecord] = {}


def _zetaris_live_flag() -> str:
    return os.environ.get("ZETARIS_LIVE", "").strip().lower()


def _zetaris_rest_enabled() -> bool:
    flag = _zetaris_live_flag()
    live_flag = flag in ("1", "true", "yes")
    has_key = bool(os.environ.get("ZETARIS_API_KEY", "").strip())
    return live_flag and has_key


def get_zetaris_client() -> ZetarisClient:
    """Jdbc when ``ZETARIS_LIVE=jdbc``; REST when ``ZETARIS_LIVE=1`` + API key; else STUB."""
    if _zetaris_live_flag() == "jdbc":
        return JdbcZetarisClient()
    if _zetaris_rest_enabled():
        return RestZetarisClient()
    return StubZetarisClient()


def zetaris_backend_label(client: ZetarisClient) -> str:
    if isinstance(client, JdbcZetarisClient):
        return "ZETARIS-JDBC"
    if isinstance(client, RestZetarisClient):
        return "Rest"
    return "STUB"


def is_replay_mode() -> bool:
    return os.environ.get("WHYLEDGER_MODE", "").strip().lower() == "replay"


def resolve_mode(client: ZetarisClient | None, llm: LLMClient | None) -> Mode:
    """Operational mode for cards and UI badges."""
    explicit = os.environ.get("WHYLEDGER_MODE", "").strip().lower()
    if explicit in ("live", "replay", "stub"):
        return explicit  # type: ignore[return-value]
    if client is None or llm is None:
        return "stub"
    from whyledger.llm import RecordedStubLLM

    from whyledger.llm import nvidia_direct_configured

    model_live = bool(
        os.environ.get("METERLESS_RUNTIME_TOKEN", "").strip() or nvidia_direct_configured()
    )
    if isinstance(client, JdbcZetarisClient):
        return "live"
    if isinstance(client, RestZetarisClient) and model_live:
        return "live"

    if isinstance(llm, RecordedStubLLM):
        return "replay" if os.environ.get("WHYLEDGER_REPLAY", "").strip() == "1" else "stub"
    return "stub"


def _trace_payload(event: TraceEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "step": event.step,
        "tool": event.tool,
        "sql": event.sql,
        "row_count": event.row_count,
        "note": event.note,
        "intent": event.intent,
        "routing_hash": event.routing_hash,
    }
    if event.provider:
        payload["provider"] = event.provider
    if event.model:
        payload["model"] = event.model
    return payload


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _evidence_id_for_sql(ledger: Ledger, sql: str | None) -> str | None:
    if not sql:
        return None
    norm = sql.strip()
    for ev in ledger.evidence_map.values():
        if ev.sql.strip() == norm:
            return ev.query_id
    return None


def _stream_replay_session(session: ReplaySession) -> Iterator[str]:
    """SSE for stub or recorded-live replay bundles."""
    if session.replay_source == "recorded_live":
        yield _format_sse(
            "replay_info",
            {
                "source": "recorded_live",
                "mode": "recorded_live",
                "label": "Recorded live run",
                "banner": recorded_live_banner_text(session.bundle),
                "recorded_at": session.bundle.recorded_at,
                "backends": session.bundle.backends,
                "latency_ms": session.bundle.latency_ms,
                "llm_calls": sum(
                    1 for ev in session.trace if ev.tool == "draft_claims"
                ),
                "is_synthetic": True,
            },
        )

    for ev in session.trace:
        payload = _trace_payload(ev)
        if ev.tool == "scout_intent":
            yield _format_sse("scout_intent", payload)
        if ev.sql and ev.tool in ("planned_evidence", "sql", "policy_lookup", "peer_cases"):
            qid = _evidence_id_for_sql(session.ledger, ev.sql)
            yield _format_sse(
                "evidence_query",
                {
                    **payload,
                    "evidence_id": qid,
                    "sql": ev.sql,
                },
            )
        if ev.tool == "draft_claims":
            drafted = [
                {"id": c.id, "text": c.text, "query_ids": list(c.query_ids)}
                for c in session.ledger.claims
            ]
            yield _format_sse(
                "claims_drafted",
                {"count": len(drafted), "claims": drafted},
            )
        yield _format_sse("trace", payload)

    for ver in session.verification:
        claim = next(
            (c for c in session.ledger.claims if c.id == ver.claim_id),
            None,
        )
        yield _format_sse(
            "verification",
            {
                "claim_id": ver.claim_id,
                "claim_text": claim.text if claim else "",
                "status": ver.status,
                "reason": ver.reason,
                "shipped": ver.status == "VERIFIED",
            },
        )

    client = get_zetaris_client()
    card = enrich_why_card(
        session.card,
        client=client,
        loan_id=session.loan_id,
        ledger=session.ledger,
    )
    card_id = _store_card(card, session.ledger, session.verification)
    card_payload = card.model_dump()
    card_payload["card_id"] = card_id
    if session.replay_source == "recorded_live":
        card_payload["replay_source"] = "recorded_live"
        card_payload["replay_banner"] = recorded_live_banner_text(session.bundle)
    yield _format_sse("card", card_payload)


def _store_card(card: WhyCard, ledger: Ledger, verification: list[VerificationResult]) -> str:
    card_id = str(uuid.uuid4())
    _cards[card_id] = _CardRecord(card=card, ledger=ledger, verification=verification)
    return card_id


def _find_claim_record(claim_id: str) -> tuple[_CardRecord, str] | None:
    for rec in _cards.values():
        for claim in rec.ledger.claims:
            if claim.id == claim_id:
                return rec, claim_id
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="Why-Ledger", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        if is_replay_mode():
            bundles = list(default_replay_dir().glob("LN-*.json"))
            return {
                "status": "ok" if bundles else "degraded",
                "zetaris": {"backend": "Replay", "ok": bool(bundles)},
                "model": {"label": "Replay", "ok": True},
                "mode": "replay",
            }
        client = get_zetaris_client()
        llm = get_llm()
        mode = resolve_mode(client, llm)
        z_label = zetaris_backend_label(client)
        z_ok = True
        if isinstance(client, (RestZetarisClient, JdbcZetarisClient)):
            z_ok = client.health_probe()
        elif isinstance(client, StubZetarisClient):
            try:
                client.run_sql("SELECT 1 AS ok")
            except Exception:
                z_ok = False
        return {
            "status": "ok" if z_ok else "degraded",
            "zetaris": {"backend": z_label, "ok": z_ok},
            "model": {"label": llm.label, "ok": True},
            "mode": mode,
        }

    @app.post("/ask")
    def ask(body: AskBody) -> StreamingResponse:
        if not body.question and not body.loan_id:
            raise HTTPException(
                status_code=422,
                detail="question or loan_id is required",
            )

        def event_stream() -> Iterator[str]:
            if is_replay_mode():
                loan_hint = (body.loan_id or "").strip().upper()
                if not loan_hint and body.question:
                    match = re.search(r"\b(LN-\d{4})\b", body.question, re.I)
                    if match:
                        loan_hint = match.group(1).upper()
                if not loan_hint:
                    yield _format_sse(
                        "error",
                        {"message": "replay mode requires loan_id or question with LN-xxxx"},
                    )
                    return
                player = Player()
                try:
                    session = player.play(loan_hint)
                except FileNotFoundError as exc:
                    yield _format_sse("error", {"message": str(exc)})
                    return
                yield _format_sse(
                    "replay_info",
                    {
                        "source": "stub",
                        "mode": "stub_replay",
                        "label": "STUB replay",
                        "banner": "Replay (STUB bundles — recorded LLM fixtures, not live Zetaris/Nemotron)",
                        "is_synthetic": True,
                    },
                )
                yield from _stream_replay_session(session)
                return

            client = get_zetaris_client()
            llm = get_llm()
            mode = resolve_mode(client, llm)
            inv = Investigator(
                loan_id=body.loan_id,
                question=body.question,
                client=client,
                llm=llm,
                mode=mode,
            )
            try:
                result = inv.run()
            except ValueError as exc:
                yield _format_sse("error", {"message": str(exc)})
                return

            for ev in result.trace:
                payload = _trace_payload(ev)
                if ev.tool == "scout_intent":
                    yield _format_sse("scout_intent", payload)
                yield _format_sse("trace", payload)

            if result.refused:
                yield _format_sse(
                    "refusal",
                    {
                        "refused": True,
                        "message": result.refusal_message or "Request refused",
                        "intent": result.routing.intent if result.routing else None,
                    },
                )
                return

            if result.card is None:
                yield _format_sse("error", {"message": "investigation produced no card"})
                return

            verification = verify_claims(
                inv.ledger.claims,
                inv.ledger,
                client,
                subject_loan_id=inv.loan_id,
            )
            drafted = [
                {"id": c.id, "text": c.text, "query_ids": list(c.query_ids)}
                for c in inv.ledger.claims
            ]
            yield _format_sse(
                "claims_drafted",
                {"count": len(drafted), "claims": drafted},
            )
            for ver in verification:
                claim = next(
                    (c for c in inv.ledger.claims if c.id == ver.claim_id),
                    None,
                )
                yield _format_sse(
                    "verification",
                    {
                        "claim_id": ver.claim_id,
                        "claim_text": claim.text if claim else "",
                        "status": ver.status,
                        "reason": ver.reason,
                        "shipped": ver.status == "VERIFIED",
                    },
                )
            card_id = _store_card(result.card, inv.ledger, verification)
            card_payload = result.card.model_dump()
            card_payload["card_id"] = card_id
            yield _format_sse("card", card_payload)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/replay/recorded-live")
    def recorded_live_catalog() -> dict[str, Any]:
        loans = list_recorded_live_loans()
        nightshift_short = recorded_live_nightshift_radio_label()
        return {
            "is_synthetic": True,
            "loans": loans,
            "nightshift_radio_label": nightshift_short,
            "variants": [
                {
                    "id": "nightshift",
                    "label": (
                        f"{nightshift_short} — "
                        "nvidia/nemotron-3.5-lightning-30b-a3b via Meterless Nightshift"
                    ),
                },
                {
                    "id": "nvidia_direct",
                    "label": (
                        "NVIDIA-direct run (23 Oct, earlier) — "
                        "nvidia/nemotron-3.5-lightning-30b-a3b via NVIDIA API"
                    ),
                },
            ],
        }

    @app.get("/verifier/stress")
    def verifier_stress_catalog() -> dict[str, Any]:
        payload = load_stress_json()
        if payload is None:
            return {
                "label": "PLANTED test claims, not model output",
                "is_synthetic": True,
                "rows": [],
                "totals": {},
                "message": "Run scripts/stress_verifier.py to generate eval/verifier-stress.json",
            }
        return payload

    @app.post("/replay/recorded-live")
    def recorded_live_replay(body: RecordedLiveReplayBody) -> StreamingResponse:
        loan_id = body.loan_id.strip().upper()
        if not re.fullmatch(r"LN-\d{4}", loan_id):
            raise HTTPException(status_code=422, detail="loan_id must be LN-xxxx")
        variant = body.variant

        def event_stream() -> Iterator[str]:
            player = Player()
            try:
                session = player.play_recorded_live(loan_id, variant=variant)
            except FileNotFoundError as exc:
                yield _format_sse("error", {"message": str(exc)})
                return
            yield from _stream_replay_session(session)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/card/{card_id}")
    def get_card(card_id: str) -> dict[str, Any]:
        rec = _cards.get(card_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="card not found")
        payload = rec.card.model_dump()
        payload["card_id"] = card_id
        return payload

    @app.get("/evidence/{claim_id}")
    def get_evidence(claim_id: str) -> dict[str, Any]:
        found = _find_claim_record(claim_id)
        if found is None:
            raise HTTPException(status_code=404, detail="claim not found")
        rec, cid = found
        claim = next(c for c in rec.ledger.claims if c.id == cid)
        ver = next(
            (v for v in rec.verification if v.claim_id == cid),
            None,
        )
        queries: list[dict[str, Any]] = []
        for qid in claim.query_ids:
            ev = rec.ledger.evidence_map.get(qid)
            if ev is None:
                continue
            queries.append(
                {
                    "query_id": ev.query_id,
                    "sql": ev.sql,
                    "columns": ev.columns,
                    "rows": ev.rows,
                    "sha256": ev.sha256,
                }
            )
        return {
            "claim_id": cid,
            "claim_text": claim.text,
            "verification": {
                "status": ver.status if ver else "UNKNOWN",
                "reason": ver.reason if ver else "",
            },
            "queries": queries,
        }

    @app.get("/")
    def index() -> FileResponse:
        path = _UI_DIR / "index.html"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="UI not found")
        return FileResponse(path, media_type="text/html")

    app.mount("/ui", StaticFiles(directory=_UI_DIR), name="ui_static")

    return app


app = create_app()
