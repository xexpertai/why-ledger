# Live planned-mode eval via Meterless Nightshift (SYNTHETIC demo)

**Recorded:** 2026-10-25T17:07:14Z

**Data:** SYNTHETIC fictional lender Harbor Credit — not real customers.

**Backends (true live):**

- Zetaris: `ZETARIS_LIVE=jdbc`
- Model: **Meterless Nightshift** (`METERLESS_RUNTIME_TOKEN` set; `METERLESS_MODEL` → Nemotron on this account)
- On this account: `nvidia/nemotron-3.5-lightning-30b-a3b` works; `nvidia/nemotron-3-super-120b-a12b` returns **404**; `llama-3.3-nemotron-super-49b` returns **410**.
- Agent: `LIVE_AGENT_MODE=planned` (code-planned evidence, model drafts claims)

## Per-loan runs (one each)

| Loan | Provider | Model | LLM calls | Drafted | Verified shipped | Rejected | Top driver (card) | Truth top driver | Latency (ms) |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: |
| LN-1042 | meterless-nightshift | nvidia/nemotron-3.5-lightning-30b-a3b | 1 | 6 | 6 | 0 | late_over_15_of_last_6 | late_over_15_of_last_6 (yes) | 10150.46 |
| LN-1107 | meterless-nightshift | nvidia/nemotron-3.5-lightning-30b-a3b | 1 | 5 | 5 | 0 | defaults | defaults (yes) | 6714.09 |
| LN-1230 | meterless-nightshift | nvidia/nemotron-3.5-lightning-30b-a3b | 1 | 6 | 6 | 0 | amount_to_income | amount_to_income (yes) | 7775.12 |
| LN-1301 | meterless-nightshift | nvidia/nemotron-3.5-lightning-30b-a3b | 1 | 5 | 5 | 0 | bureau_score | bureau_score (yes) | 6758.14 |

Replay bundles: `replay/LN-1042.live-nightshift-planned.json`, `replay/LN-1107.live-nightshift-planned.json`, `replay/LN-1230.live-nightshift-planned.json`, `replay/LN-1301.live-nightshift-planned.json`

Drivers on the card are ranked from scorecard points in Zetaris data, not from the LLM.
