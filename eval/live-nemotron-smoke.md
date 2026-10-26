# Live Nemotron smoke (2026-10-23)

**Backends (true live):**

- Zetaris: **ZETARIS-JDBC** (`ZETARIS_LIVE=jdbc`)
- Model: **Nemotron via integrate.api.nvidia.com** (`nvidia/nemotron-3.5-lightning-30b-a3b`, `LLM_ROUTE=nvidia`)
- Connection probe: `scripts/test_connection.py` → **PASS** `nvidia/nemotron-3.5-lightning-30b-a3b`

## LN-1042 investigation (single run, no retry)

- LLM calls: **4** (live agent cap)
- Trace: `replay/LN-1042.live-nemotron.json`

### What Nemotron proposed

All four turns were `step: tool` / `sql` (no `plan`, no `draft_claims`):

1. `SELECT outcome FROM WHY_LEDGER_SYN.applications WHERE loan_id = 'LN-1042'` — succeeded (`decline`).
2. Duplicate outcome query.
3. `SELECT outcome, reason_code ...` — succeeded (`decline`, `UTILISATION_AND_CONDUCT`).
4. Duplicate outcome/reason_code query.

The first live attempt (before SQL error handling) crashed on malformed SQL (`...'LN-1042'}` with a stray `}` and column `reason`); that run was **not** saved. The recorded trace is from the **second** one-shot run after the agent was taught to trace JDBC failures instead of crashing.

### Claims and verifier

- **Drafted claims:** 0 (model never emitted `draft_claims` within the 4-call budget).
- **Verified shipped:** 0
- **Rejected:** 0

The pipeline still built a WhyCard with **decision `decline`** (from Zetaris), scorecard **drivers** from data (not from the LLM), and counterfactual search where applicable. No unsupported claims shipped — verifier rules unchanged.

### Honest assessment

Nemotron **did not** complete the investigator workflow on this single smoke: it repeated safe SELECTs instead of gathering bureau/payment evidence and drafting claims. For demo quality, keep **Replay** (STUB fixture) for LN-1042’s verifier trap narrative, or raise the live call budget / tighten the prompt further. The NVIDIA route and JDBC stack are wired; model behaviour on this run was weak, not masked.
