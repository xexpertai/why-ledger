#!/usr/bin/env python3
"""One-shot live Nemotron planned-evidence run for LN-1042 (no retry loop)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whyledger.agent import Investigator
from whyledger.api import get_zetaris_client, resolve_mode, zetaris_backend_label
from whyledger.llm import get_llm
from whyledger.prompts import resolve_live_agent_mode
from whyledger.replay import Recorder
from whyledger.verifier import verify_claims


def main() -> int:
    loan_id = "LN-1042"
    client = get_zetaris_client()
    llm = get_llm()
    mode = resolve_mode(client, llm)
    agent_mode = resolve_live_agent_mode(llm)
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
        "live_agent_mode": agent_mode,
    }
    dest = ROOT / "replay" / "LN-1042.live-nemotron-planned.json"
    dest.write_text(
        json.dumps(bundle.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    drafted = len(inv.ledger.claims)
    verified = len(result.card.claims)
    rejected = len(result.card.rejected)
    eval_path = ROOT / "eval" / "live-nemotron-planned.md"
    eval_path.write_text(
        _eval_md(
            llm_calls=llm.call_count,
            agent_mode=agent_mode,
            drafted=drafted,
            verified=verified,
            rejected=rejected,
            trace_steps=len(result.trace),
        ),
        encoding="utf-8",
    )
    print(f"agent_mode={agent_mode}")
    print(f"llm_calls={llm.call_count}")
    print(f"trace_steps={len(result.trace)}")
    print(f"drafted_claims={drafted}")
    print(f"verified_shipped={verified}")
    print(f"rejected={rejected}")
    print(f"saved={dest}")
    print(f"eval={eval_path}")
    return 0


def _eval_md(
    *,
    llm_calls: int,
    agent_mode: str,
    drafted: int,
    verified: int,
    rejected: int,
    trace_steps: int,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""# Live Nemotron planned evidence (SYNTHETIC demo)

**Recorded:** {now} (UTC)

**Backends (true live):**

- Zetaris: **ZETARIS-JDBC** (`ZETARIS_LIVE=jdbc`)
- Model: **Nemotron via integrate.api.nvidia.com** (`LLM_ROUTE=nvidia`)
- Agent: **`LIVE_AGENT_MODE={agent_mode}`** (code-planned evidence, model drafts claims)

## LN-1042 investigation (single run)

- LLM calls: **{llm_calls}** (planned cap ≤ 2)
- Trace steps: **{trace_steps}**
- Trace: `replay/LN-1042.live-nemotron-planned.json`

### Claims and verifier

- **Drafted claims:** {drafted}
- **Verified shipped:** {verified}
- **Rejected:** {rejected}

Verifier re-runs each claim SQL unchanged; unsupported claims are not shipped.
"""


if __name__ == "__main__":
    raise SystemExit(main())
