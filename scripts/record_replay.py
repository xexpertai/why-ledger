#!/usr/bin/env python3
"""Record replay bundles for the four planted SYNTHETIC loans."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whyledger.agent import Investigator
from whyledger.api import get_zetaris_client, resolve_mode
from whyledger.llm import RecordedStubLLM
from whyledger.replay import Recorder, record_investigator_run

PLANTED = ("LN-1042", "LN-1107", "LN-1230", "LN-1301")


def fixture_path(loan_id: str) -> Path:
    slug = loan_id.replace("-", "").lower()
    return ROOT / "fixtures" / f"llm_{slug}.json"


def main() -> int:
    out = ROOT / "data" / "out"
    if not (out / "applications.csv").is_file():
        print("data/out missing; run: make data", file=sys.stderr)
        return 1

    client = get_zetaris_client()
    recorder = Recorder(replay_dir=ROOT / "replay")
    written: list[Path] = []

    for loan_id in PLANTED:
        path = fixture_path(loan_id)
        if not path.is_file():
            print(f"missing fixture {path}", file=sys.stderr)
            return 1
        llm: RecordedStubLLM = RecordedStubLLM(fixture_path=path)
        mode = resolve_mode(client, llm)
        inv = Investigator(
            loan_id=loan_id,
            client=client,
            llm=llm,
            mode=mode,
        )
        result = inv.run()
        if result.card is None:
            print(f"{loan_id}: no card produced", file=sys.stderr)
            return 1
        dest = record_investigator_run(inv, result=result, client=client, recorder=recorder)
        written.append(dest)
        print(f"recorded {loan_id} -> {dest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
