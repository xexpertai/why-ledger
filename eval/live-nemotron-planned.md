# Live Nemotron planned evidence (SYNTHETIC demo)

**Recorded:** 2026-10-23 (UTC)

**Backends (true live):**

- Zetaris: **ZETARIS-JDBC** (`ZETARIS_LIVE=jdbc`)
- Model: **Nemotron via integrate.api.nvidia.com** (`LLM_ROUTE=nvidia`)
- Agent: **`LIVE_AGENT_MODE=planned`** (code-planned evidence, model drafts claims)

## LN-1042 investigation (single run)

- LLM calls: **1** (planned cap ≤ 2)
- Trace steps: **8**
- Trace: `replay/LN-1042.live-nemotron-planned.json`

### Claims and verifier

- **Drafted claims:** 10
- **Verified shipped:** 10
- **Rejected:** 0

Verifier re-runs each claim SQL unchanged; unsupported claims are not shipped.

### Score labelling (LN-1042)

Earlier planned runs drafted **"credit score is 105.0"** from `applications.score`. That column is the **internal scorecard total** (105.0 points), not the bureau score (670 from `bureau.score`, exposed as `bureau_score` in planned evidence SQL). Evidence SQL now aliases `score AS scorecard_points` on applications; this run drafted **"scorecard points is 105.0"** and **"bureau score is 670"** separately.
