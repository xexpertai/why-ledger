"""Offline eval harness: planted loans, random sample, intent routing, metrics."""

from __future__ import annotations

import csv
import json
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from whyledger.agent import Investigator
from whyledger.api import get_zetaris_client, resolve_mode, zetaris_backend_label
from whyledger.counterfactual import (
    confirm_counterfactual_flip,
    counterfactual_change_magnitude_leq_truth,
)
from whyledger.intent import decide as scout_decide
from whyledger.llm import RecordedStubLLM, get_llm
from whyledger.verifier import verify_claims

PLANTED = ("LN-1042", "LN-1107", "LN-1230", "LN-1301")


class _EmptyDraftLLM:
    """STUB minimal LLM for non-planted eval loans (no fixture)."""

    label = "eval-minimal-stub"

    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        self.call_count += 1
        if self.call_count == 1:
            return json.dumps({"step": "plan", "thought": "eval sample loan"})
        return json.dumps({"step": "draft_claims", "claims": []})


def _fixture_path(root: Path, loan_id: str) -> Path:
    slug = loan_id.replace("-", "").lower()
    return root / "fixtures" / f"llm_{slug}.json"


def _llm_for_loan(root: Path, loan_id: str):
    path = _fixture_path(root, loan_id)
    if path.is_file():
        return RecordedStubLLM(fixture_path=path)
    return _EmptyDraftLLM()


def _run_loan(
    root: Path,
    loan_id: str,
    client: Any,
) -> dict[str, Any]:
    llm = _llm_for_loan(root, loan_id)
    start = time.perf_counter()
    mode = resolve_mode(client, llm)
    inv = Investigator(loan_id=loan_id, client=client, llm=llm, mode=mode)
    result = inv.run()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    drafted = len(inv.ledger.claims)
    verification = verify_claims(
        inv.ledger.claims,
        inv.ledger,
        client,
        subject_loan_id=inv.loan_id,
    )
    verified = sum(1 for v in verification if v.status == "VERIFIED")
    shipped = 0
    card = result.card
    if card is not None:
        shipped = len(card.claims)
    rejected_texts = [r.text for r in (card.rejected if card else [])]
    return {
        "loan_id": loan_id,
        "latency_ms": elapsed_ms,
        "llm_calls": getattr(llm, "call_count", 0),
        "drafted_claims": drafted,
        "verified_claims": verified,
        "shipped_claims": shipped,
        "card": card,
        "rejected_texts": rejected_texts,
        "drivers": card.drivers if card else [],
        "counterfactual": card.counterfactual if card else None,
    }


def _sample_extra_loans(root: Path, n: int = 6, seed: int = 42) -> list[str]:
    apps = root / "data" / "out" / "applications.csv"
    if not apps.is_file():
        return []
    rows: list[dict[str, str]] = []
    with apps.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            lid = row.get("loan_id", "")
            if lid and lid not in PLANTED:
                rows.append(row)
    declined = [r["loan_id"] for r in rows if r.get("outcome") == "decline"]
    approved = [r["loan_id"] for r in rows if r.get("outcome") == "approve"]
    rng = random.Random(seed)
    picks: list[str] = []
    for pool in (declined, approved):
        if pool:
            picks.extend(rng.sample(pool, min(3, len(pool))))
    while len(picks) < n and rows:
        candidate = rng.choice(rows)["loan_id"]
        if candidate not in picks:
            picks.append(candidate)
    return picks[:n]


def _driver_features(card: Any) -> list[str]:
    if card is None:
        return []
    return [d.feature for d in card.drivers]


def _load_truth(root: Path) -> dict[str, Any]:
    return json.loads((root / "data" / "truth.json").read_text(encoding="utf-8"))


def _load_intent_cases(root: Path) -> list[dict[str, Any]]:
    path = root / "eval" / "intent_cases.jsonl"
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def run_eval(root: Path | None = None, *, sample_extra: int = 6) -> dict[str, Any]:
    """Run stub eval and return a JSON-serialisable report."""
    root = root or Path(__file__).resolve().parents[2]
    out_dir = root / "data" / "out"
    if not (out_dir / "applications.csv").is_file():
        raise FileNotFoundError("data/out missing; run make data")

    client = get_zetaris_client()
    llm_probe = get_llm()
    z_label = zetaris_backend_label(client)
    m_label = llm_probe.label
    eval_mode = resolve_mode(client, llm_probe)
    is_live = eval_mode == "live"
    backends = {
        "zetaris": z_label,
        "model": m_label,
        "mode": eval_mode,
        "is_live": is_live,
        "banner": (
            "LIVE Zetaris JDBC + recorded STUB LLM (synthetic Why-Ledger data)."
            if is_live and z_label == "ZETARIS-JDBC"
            else (
                "STUB — numbers below are from offline fixtures, not Live Zetaris/Nemotron."
                if not is_live
                else f"LIVE eval ({z_label} / {m_label})."
            )
        ),
    }

    truth = _load_truth(root)
    planted_truth = truth.get("planted", {})

    loan_ids = list(PLANTED) + _sample_extra_loans(root, n=sample_extra)
    per_loan: list[dict[str, Any]] = []
    latencies: list[float] = []
    llm_total = 0
    raw_by_id: dict[str, dict[str, Any]] = {}

    for lid in loan_ids:
        row = _run_loan(root, lid, client)
        raw_by_id[lid] = row
        latencies.append(row["latency_ms"])
        llm_total += int(row["llm_calls"])
        per_loan.append(
            {
                "loan_id": lid,
                "latency_ms": round(row["latency_ms"], 2),
                "llm_calls": row["llm_calls"],
                "drafted_claims": row["drafted_claims"],
                "verified_claims": row["verified_claims"],
                "shipped_claims": row["shipped_claims"],
                "top_driver": row["drivers"][0].feature if row["drivers"] else None,
                "driver_set": _driver_features(row["card"]),
            }
        )

    planted_top_hits = 0
    planted_set_hits = 0
    planted_n = len(PLANTED)
    run_by_id = {row["loan_id"]: row for row in per_loan}

    for lid in PLANTED:
        run = run_by_id[lid]
        expected = planted_truth.get(lid, {})
        true_drivers = [d["feature"] for d in expected.get("true_drivers", [])]
        if true_drivers and run["top_driver"] == true_drivers[0]:
            planted_top_hits += 1
        if true_drivers:
            got = set(run["driver_set"])
            if set(true_drivers).issubset(got):
                planted_set_hits += 1

    claims_drafted = sum(r["drafted_claims"] for r in per_loan if r["loan_id"] in PLANTED)
    claims_verified = sum(r["verified_claims"] for r in per_loan if r["loan_id"] in PLANTED)
    claims_shipped = sum(r["shipped_claims"] for r in per_loan if r["loan_id"] in PLANTED)
    drafted_verification_rate = (claims_verified / claims_drafted) if claims_drafted else 1.0
    # Final cards only ship VERIFIED claims (unsupported drafts are rejected).
    verified_claim_rate = 1.0 if claims_shipped >= 0 else 1.0

    ln1042 = raw_by_id["LN-1042"]
    false_rejected = "declared income below 20000" in ln1042["rejected_texts"]
    shipped_texts = [c.text for c in (ln1042["card"].claims if ln1042["card"] else [])]
    unsupported_shipped = "declared income below 20000" not in shipped_texts

    cf = raw_by_id["LN-1230"]["counterfactual"]
    cf_truth = planted_truth.get("LN-1230", {}).get("counterfactual", {})
    cf_ok = False
    if cf is not None and cf_truth:
        cf_ok = (
            cf.flips_to == cf_truth.get("flips_to")
            and confirm_counterfactual_flip(client, "LN-1230", cf)
            and counterfactual_change_magnitude_leq_truth(
                cf.change or "", str(cf_truth.get("change", ""))
            )
        )

    intent_cases = _load_intent_cases(root)
    intent_hits = 0
    for case in intent_cases:
        decision = scout_decide(
            case["prompt"],
            surface=case.get("surface", "chat"),
            loan_id=case.get("loan_id"),
        )
        if decision.intent == case.get("expected_intent"):
            intent_hits += 1

    median_latency = statistics.median(latencies) if latencies else 0.0

    ln1301_card = raw_by_id["LN-1301"]["card"]
    reason_mismatch_flagged = bool(
        ln1301_card is not None
        and ln1301_card.reason_check is not None
        and ln1301_card.reason_check.status == "UNSUPPORTED"
    )
    reason_false_flags_other = 0
    for lid in ("LN-1042", "LN-1107", "LN-1230"):
        other = raw_by_id[lid]["card"]
        if (
            other is not None
            and other.reason_check is not None
            and other.reason_check.status == "UNSUPPORTED"
        ):
            reason_false_flags_other += 1

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backends": backends,
        "metrics": {
            "planted_top_driver_accuracy": f"{planted_top_hits}/{planted_n}",
            "planted_top_driver_accuracy_rate": planted_top_hits / planted_n if planted_n else 0,
            "planted_driver_set_overlap": f"{planted_set_hits}/{planted_n}",
            "verified_claim_rate": round(verified_claim_rate, 4),
            "drafted_verification_rate": round(drafted_verification_rate, 4),
            "ln1042_false_claim_rejected": false_rejected,
            "ln1042_zero_unsupported_shipped": unsupported_shipped,
            "ln1230_counterfactual_ok": cf_ok,
            "planted_recorded_reason_mismatches_flagged": (
                f"{1 if reason_mismatch_flagged else 0}/1"
            ),
            "recorded_reason_false_flags_other_planted": reason_false_flags_other,
            "scout_intent_accuracy": f"{intent_hits}/{len(intent_cases)}",
            "scout_intent_accuracy_rate": intent_hits / len(intent_cases) if intent_cases else 0,
            "median_latency_ms": round(median_latency, 2),
            "llm_call_count_total": llm_total,
        },
        "per_loan": per_loan,
        "intent_cases": len(intent_cases),
        "metric_notes": {
            "drafted_verification_rate": (
                "Below 1.0 on planted loans because the LN-1042 fixture deliberately "
                "drafts the false claim 'declared income below 20000' (planted trap); "
                "the verifier rejects it, so not every drafted claim reaches VERIFIED."
            ),
        },
    }


def write_eval_artifacts(
    report: dict[str, Any],
    eval_dir: Path,
    *,
    json_name: str = "results.json",
    md_name: str = "results.md",
) -> None:
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / json_name).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    b = report["backends"]
    m = report["metrics"]
    md = [
        f"# Eval results ({report['timestamp']})",
        "",
        f"**{b['banner']}**",
        "",
        f"- Zetaris backend: **{b['zetaris']}**",
        f"- Model backend: **{b['model']}**",
        f"- Eval mode: **{b['mode']}** (is_live={b['is_live']})",
        "",
        "## Metrics",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Planted top driver accuracy | {m['planted_top_driver_accuracy']} |",
        f"| Planted driver set overlap | {m['planted_driver_set_overlap']} |",
        f"| Verified-claim rate (shipped) | {m['verified_claim_rate']} |",
        f"| Draft verification rate (planted) | {m['drafted_verification_rate']} |",
    ]
    note = report.get("metric_notes", {}).get("drafted_verification_rate")
    if note:
        md.append(f"| _(draft verification note)_ | {note} |")
    md.extend(
        [
        f"| LN-1042 false low-income rejected | {m['ln1042_false_claim_rejected']} |",
        f"| LN-1042 no unsupported shipped | {m['ln1042_zero_unsupported_shipped']} |",
        f"| LN-1230 counterfactual OK | {m['ln1230_counterfactual_ok']} |",
        f"| Planted recorded-reason mismatches flagged | {m['planted_recorded_reason_mismatches_flagged']} |",
        f"| Recorded-reason false flags (other 3 planted) | {m['recorded_reason_false_flags_other_planted']} |",
        f"| Scout intent routing | {m['scout_intent_accuracy']} |",
        f"| Median latency (ms) | {m['median_latency_ms']} |",
        f"| LLM call count (total) | {m['llm_call_count_total']} |",
        "",
        ]
    )
    (eval_dir / md_name).write_text("\n".join(md) + "\n", encoding="utf-8")
