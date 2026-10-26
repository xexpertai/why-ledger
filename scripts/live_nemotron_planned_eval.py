#!/usr/bin/env python3
"""Live planned-evidence eval for all planted loans (one run each)."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whyledger.agent import Investigator
from whyledger.api import get_zetaris_client, resolve_mode, zetaris_backend_label
from whyledger.eval_harness import PLANTED
from whyledger.llm import (
    MeterlessRuntimeClient,
    get_llm,
    llm_backend_fields,
    llm_trace_meta,
)
from whyledger.prompts import resolve_live_agent_mode
from whyledger.replay import (
    RECORDED_LIVE_NEMOTRON_SUFFIX,
    RECORDED_LIVE_NIGHTSHIFT_SUFFIX,
    Recorder,
)
from whyledger.verifier import verify_claims

PLANTED_LOANS = tuple(PLANTED)


def _truth_top_driver(root: Path, loan_id: str) -> str | None:
    truth = json.loads((root / "data" / "truth.json").read_text(encoding="utf-8"))
    planted = truth.get("planted", {}).get(loan_id, {})
    drivers = planted.get("true_drivers") or []
    if not drivers:
        return None
    return str(drivers[0].get("feature"))


def _replay_suffix(llm: object) -> str:
    if isinstance(llm, MeterlessRuntimeClient):
        return RECORDED_LIVE_NIGHTSHIFT_SUFFIX
    return RECORDED_LIVE_NEMOTRON_SUFFIX


def _results_path(llm: object) -> Path:
    if isinstance(llm, MeterlessRuntimeClient):
        return ROOT / "eval" / "results-live-nightshift.md"
    return ROOT / "eval" / "results-live-planned.md"


def _run_one(loan_id: str) -> dict[str, object]:
    client = get_zetaris_client()
    llm = get_llm()
    mode = resolve_mode(client, llm)
    provider, model_id = llm_trace_meta(llm)
    start = time.perf_counter()
    inv = Investigator(
        loan_id=loan_id,
        question=f"Explain the decision for {loan_id}.",
        client=client,
        llm=llm,
        mode=mode,
    )
    try:
        result = inv.run()
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        suffix = _replay_suffix(llm)
        return {
            "loan_id": loan_id,
            "error": str(exc),
            "latency_ms": round(elapsed_ms, 2),
            "llm_calls": llm.call_count,
            "provider": provider or "—",
            "model": model_id or llm.label,
            "drafted": 0,
            "verified_shipped": 0,
            "rejected": 0,
            "top_driver": None,
            "truth_top_driver": _truth_top_driver(ROOT, loan_id),
            "trace_steps": 0,
            "replay": f"replay/{loan_id}{suffix}",
        }
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    verification = verify_claims(inv.ledger.claims, inv.ledger, client)
    recorder = Recorder(replay_dir=ROOT / "replay")
    suffix = _replay_suffix(llm)
    replay_name = f"{loan_id}{suffix}"
    if result.card is not None:
        bundle = recorder.build_bundle(
            result=result,
            ledger=inv.ledger,
            verification=verification,
            zetaris=client,
            model_label=llm.label,
        )
        bundle.backends = {
            "zetaris": zetaris_backend_label(client),
            **llm_backend_fields(llm),
            "live_agent_mode": resolve_live_agent_mode(llm),
        }
        bundle.latency_ms = round(elapsed_ms, 2)
        dest = ROOT / "replay" / replay_name
        dest.write_text(
            json.dumps(bundle.model_dump(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    drafted = len(inv.ledger.claims)
    shipped = len(result.card.claims) if result.card else 0
    rejected = len(result.card.rejected) if result.card else 0
    top_driver = (
        result.card.drivers[0].feature
        if result.card and result.card.drivers
        else None
    )
    provider, model_id = llm_trace_meta(llm)
    return {
        "loan_id": loan_id,
        "latency_ms": round(elapsed_ms, 2),
        "llm_calls": llm.call_count,
        "trace_steps": len(result.trace),
        "drafted": drafted,
        "verified_shipped": shipped,
        "rejected": rejected,
        "top_driver": top_driver,
        "truth_top_driver": _truth_top_driver(ROOT, loan_id),
        "provider": provider or "—",
        "model": model_id or llm.label,
        "replay": f"replay/{replay_name}",
    }


def _md(rows: list[dict[str, object]], agent_mode: str, nightshift: bool) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = (
        "# Live planned-mode eval via Meterless Nightshift (SYNTHETIC demo)"
        if nightshift
        else "# Live planned-mode eval (SYNTHETIC demo)"
    )
    model_line = (
        "- Model: **Meterless Nightshift** (`METERLESS_RUNTIME_TOKEN` set; "
        "`METERLESS_MODEL` → Nemotron on this account)"
        if nightshift
        else "- Model: `LLM_ROUTE=nvidia` (Nemotron via integrate.api.nvidia.com)"
    )
    if nightshift:
        model_line += (
            "\n- On this account: `nvidia/nemotron-3.5-lightning-30b-a3b` works; "
            "`nvidia/nemotron-3-super-120b-a12b` returns **404**; "
            "`llama-3.3-nemotron-super-49b` returns **410**."
        )
    lines = [
        title,
        "",
        f"**Recorded:** {now}",
        "",
        "**Data:** SYNTHETIC fictional lender Harbor Credit — not real customers.",
        "",
        "**Backends (true live):**",
        "",
        "- Zetaris: `ZETARIS_LIVE=jdbc`",
        model_line,
        f"- Agent: `LIVE_AGENT_MODE={agent_mode}` (code-planned evidence, model drafts claims)",
        "",
        "## Per-loan runs (one each)",
        "",
        "| Loan | Provider | Model | LLM calls | Drafted | Verified shipped | Rejected | Top driver (card) | Truth top driver | Latency (ms) |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for r in rows:
        if r.get("error"):
            lines.append(
                f"| {r['loan_id']} | {r['provider']} | {r['model']} | {r['llm_calls']} | "
                f"— | — | — | **FAILED** | {r['truth_top_driver']} | {r['latency_ms']} |"
            )
            continue
        match = "yes" if r["top_driver"] == r["truth_top_driver"] else "no"
        lines.append(
            f"| {r['loan_id']} | {r['provider']} | {r['model']} | {r['llm_calls']} | "
            f"{r['drafted']} | {r['verified_shipped']} | {r['rejected']} | "
            f"{r['top_driver']} | {r['truth_top_driver']} ({match}) | {r['latency_ms']} |"
        )
    failures = [r for r in rows if r.get("error")]
    if failures:
        lines.extend(["", "## Failures", ""])
        for r in failures:
            lines.append(f"- **{r['loan_id']}**: `{r['error']}`")
    bundle_paths = []
    for r in rows:
        rel = str(r["replay"])
        path = ROOT / rel
        suffix = "" if path.is_file() else " (not written — run failed)"
        bundle_paths.append(f"`{rel}`{suffix}")
    lines.extend(
        [
            "",
            "Replay bundles: " + ", ".join(bundle_paths),
            "",
            "Drivers on the card are ranked from scorecard points in Zetaris data, not from the LLM.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    llm = get_llm()
    nightshift = isinstance(llm, MeterlessRuntimeClient)
    agent_mode = resolve_live_agent_mode(llm)
    rows: list[dict[str, object]] = []
    for loan_id in PLANTED_LOANS:
        print(f"running {loan_id}...", file=sys.stderr)
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                row = _run_one(loan_id)
                if row.get("error") and attempt == 0:
                    print(
                        f"{loan_id} failed ({row['error']}); retrying once...",
                        file=sys.stderr,
                    )
                    continue
                rows.append(row)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    print(f"{loan_id} failed ({exc!r}); retrying once...", file=sys.stderr)
        if last_exc is not None:
            provider, model_id = llm_trace_meta(llm)
            rows.append(
                {
                    "loan_id": loan_id,
                    "error": str(last_exc),
                    "latency_ms": 0,
                    "llm_calls": llm.call_count,
                    "provider": provider or "—",
                    "model": model_id or llm.label,
                    "drafted": 0,
                    "verified_shipped": 0,
                    "rejected": 0,
                    "top_driver": None,
                    "truth_top_driver": _truth_top_driver(ROOT, loan_id),
                    "replay": f"replay/{loan_id}{_replay_suffix(llm)}",
                }
            )
    out = _results_path(llm)
    out.write_text(_md(rows, agent_mode, nightshift), encoding="utf-8")
    print(f"wrote {out}")
    for r in rows:
        if r.get("error"):
            print(f"{r['loan_id']}: FAILED {r['error']}")
        else:
            print(
                f"{r['loan_id']}: provider={r['provider']} model={r['model']} "
                f"llm={r['llm_calls']} drafted={r['drafted']} "
                f"shipped={r['verified_shipped']} rejected={r['rejected']} "
                f"driver={r['top_driver']} latency_ms={r['latency_ms']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
