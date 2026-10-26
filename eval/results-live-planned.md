# Live planned-mode eval (SYNTHETIC demo)

**Recorded:** 2026-10-23T15:01:39Z

**Data:** SYNTHETIC fictional lender Harbor Credit — not real customers.

**Backends (true live):**

- Zetaris: `ZETARIS_LIVE=jdbc`
- Model: `LLM_ROUTE=nvidia` (Nemotron via integrate.api.nvidia.com)
- Agent: `LIVE_AGENT_MODE=planned` (code-planned evidence, model drafts claims)

## Per-loan runs (one each)

| Loan | LLM calls | Drafted | Verified shipped | Rejected | Top driver (card) | Truth top driver | Latency (ms) | Trace |
| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |
| LN-1042 | 1 | 10 | 10 | 0 | late_over_15_of_last_6 | late_over_15_of_last_6 (yes) | 110550.65 | 8 |
| LN-1107 | 1 | 10 | 10 | 0 | defaults | defaults (yes) | 198858.1 | 8 |
| LN-1230 | 1 | 6 | 5 | 1 | amount_to_income | amount_to_income (yes) | 432539.98 | 8 |
| LN-1301 | 1 | 7 | 6 | 1 | bureau_score | bureau_score (yes) | 144796.03 | 8 |

Replay bundles: `replay/LN-1042.live-nemotron-planned.json`, `replay/LN-1107.live-nemotron-planned.json`, `replay/LN-1230.live-nemotron-planned.json`, `replay/LN-1301.live-nemotron-planned.json`

Drivers on the card are ranked from scorecard points in Zetaris data, not from the LLM.

### Verifier rejections (this run)

- **LN-1230:** one drafted claim packed multiple policy rule texts into `values` without matching row keys; verifier rejected it (5 shipped).
- **LN-1301:** one drafted peer-case claim used a composite `values` key (`peer_amounts`) that did not match evidence rows; verifier rejected it (6 shipped).

End-to-end latency includes JDBC evidence gathering plus one Nemotron draft call per loan (NVIDIA API; `NEMOTRON_HTTP_TIMEOUT=240` for this eval).
