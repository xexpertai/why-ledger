#!/usr/bin/env python3
"""Rebuild reason_check copy and applicant letters in recorded-live Nightshift bundles (offline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whyledger.card import WhyCard
from whyledger.replay import (
    RECORDED_LIVE_NIGHTSHIFT_SUFFIX,
    ReplayBundle,
    rebuild_applicant_letter,
    refresh_stored_reason_check_copy,
)
from whyledger.zetaris import StubZetarisClient

DATA_OUT = ROOT / "data" / "out"


def main() -> int:
    if not (DATA_OUT / "applications.csv").is_file():
        print("data/out missing; run data/generate.py", file=sys.stderr)
        return 1
    stub = StubZetarisClient(data_dir=DATA_OUT)
    replay_dir = ROOT / "replay"
    paths = sorted(replay_dir.glob(f"LN-*{RECORDED_LIVE_NIGHTSHIFT_SUFFIX}"))
    if not paths:
        print("No Nightshift bundles found", file=sys.stderr)
        return 1
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        bundle = ReplayBundle.model_validate(raw)
        reason = refresh_stored_reason_check_copy(bundle, stub)
        raw["card"]["reason_check"] = reason.model_dump()
        bundle = ReplayBundle.model_validate({**raw, "card": raw["card"]})
        letter = rebuild_applicant_letter(bundle)
        WhyCard.model_validate({**bundle.card, "applicant_letter": letter})
        raw["card"]["applicant_letter"] = letter
        path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"updated {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
