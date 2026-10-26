#!/usr/bin/env python3
"""Test live LLM connectivity (never prints secrets)."""

from __future__ import annotations

import sys

from whyledger.llm import MeterlessRuntimeClient, NvidiaDirectClient, get_llm


def main() -> int:
    llm = get_llm()
    if isinstance(llm, MeterlessRuntimeClient):
        ok, model, nvidia_count = llm.probe_nightshift()
        if ok:
            print(f"PASS nightshift model={model} nvidia_models_listed={nvidia_count}")
            return 0
        print(f"FAIL nightshift model={model} nvidia_models_listed={nvidia_count}")
        return 1
    if isinstance(llm, NvidiaDirectClient):
        ok, model = llm.test_connection()
        if ok:
            print(f"PASS {model}")
            return 0
        print(f"FAIL {model}")
        return 1
    print("FAIL not-configured")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
