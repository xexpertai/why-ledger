# Meterless Scout Intent (hackathon engine use)

**Engine:** [Meterless Scout Intent](https://github.com/Meterless/Meterless/tree/main/engines/scout-intent) (Apache 2.0).

**What we use:** Intent detection, risk guard, capability routing, and HMAC-signed execution contracts per `engines/scout-intent/AGENTS.md` and `docs/execution-contract.md`. Implemented in Python (not vendored TypeScript spec sources).

| Item | Location |
|---|---|
| Scout pipeline (Sense → Guard → Route → Contract) | `src/whyledger/intent.py` |
| Wired as first step of every investigator run | `src/whyledger/agent.py` (`_apply_scout_routing`, trace `tool=scout_intent`) |
| Conformance + injection port of upstream adversarial set | `tests/test_intent_conformance.py` |
| Fixture copy of `evals/regression-set/v1/injection/adversarial.jsonl` | `fixtures/scout_injection_adversarial.jsonl` |

**Proof it works:** `pytest tests/test_intent_conformance.py -q` — two intents route to different tool plans, risky prompts are refused before the LLM loop, and injection cases from the Scout eval set block at the guard.
