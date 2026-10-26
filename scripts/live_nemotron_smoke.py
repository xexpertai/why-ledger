#!/usr/bin/env python3
"""One-shot live Nemotron + JDBC investigation for LN-1042 (no retry loop)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whyledger.agent import Investigator
from whyledger.api import get_zetaris_client, resolve_mode, zetaris_backend_label
from whyledger.llm import get_llm
from whyledger.replay import Recorder
from whyledger.verifier import verify_claims


def main() -> int:
    loan_id = "LN-1042"
    client = get_zetaris_client()
    llm = get_llm()
    mode = resolve_mode(client, llm)
    inv = Investigator(
        loan_id=loan_id,
        question=f"Why was {loan_id} declined?",
        client=client,
        llm=llm,
        mode=mode,
    )
    result = inv.run()
    if result.card is None:
        print("no card", file=sys.stderr)
        return 1
    verification = verify_claims(inv.ledger.claims, inv.ledger, client)
    recorder = Recorder(replay_dir=ROOT / "replay")
    bundle = recorder.build_bundle(
        result=result,
        ledger=inv.ledger,
        verification=verification,
        zetaris=client,
        model_label=llm.label,
    )
    bundle.backends = {
        "zetaris": zetaris_backend_label(client),
        "model": llm.label,
    }
    dest = ROOT / "replay" / "LN-1042.live-nemotron.json"
    dest.write_text(
        json.dumps(bundle.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"llm_calls={llm.call_count}")
    print(f"trace_steps={len(result.trace)}")
    print(f"drafted_claims={len(inv.ledger.claims)}")
    print(f"verified_shipped={len(result.card.claims)}")
    print(f"rejected={len(result.card.rejected)}")
    print(f"saved={dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
