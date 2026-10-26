#!/usr/bin/env python3
"""Run PLANTED verifier stress claims (not model output).

Uses live Zetaris JDBC when ``ZETARIS_LIVE=jdbc``, else STUB sqlite data/out/.
Writes eval/verifier-stress.md and eval/verifier-stress.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whyledger.api import get_zetaris_client, zetaris_backend_label
from whyledger.verifier_stress import run_verifier_stress, write_stress_artifacts


def main() -> int:
    client = get_zetaris_client()
    backend = zetaris_backend_label(client)
    rows = run_verifier_stress(client)
    payload = write_stress_artifacts(
        rows,
        backend=backend,
        out_md=ROOT / "eval" / "verifier-stress.md",
        out_json=ROOT / "eval" / "verifier-stress.json",
    )
    totals = payload["totals"]
    ok = totals["expect_matched"]
    reason_ok = totals.get("reason_matched", ok)
    n = totals["cases"]
    print(
        f"PLANTED verifier stress: backend={backend} "
        f"verdict_matched={ok}/{n} reason_matched={reason_ok}/{n}"
    )
    return 0 if ok == n and reason_ok == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
