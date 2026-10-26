#!/usr/bin/env python3
"""Run offline eval and write eval/results.json + eval/results.md."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whyledger.eval_harness import run_eval, write_eval_artifacts


def main() -> int:
    try:
        report = run_eval(ROOT)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    write_eval_artifacts(report, ROOT / "eval")
    print(f"eval written to {ROOT / 'eval'}")
    print(f"planted top driver: {report['metrics']['planted_top_driver_accuracy']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
