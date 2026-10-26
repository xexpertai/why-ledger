# Eval results (2026-10-25T17:16:53.951124+00:00)

**STUB — numbers below are from offline fixtures, not Live Zetaris/Nemotron.**

- Zetaris backend: **STUB**
- Model backend: **STUB-recorded-nemotron**
- Eval mode: **stub** (is_live=False)

## Metrics

| Metric | Value |
|--------|-------|
| Planted top driver accuracy | 4/4 |
| Planted driver set overlap | 4/4 |
| Verified-claim rate (shipped) | 1.0 |
| Draft verification rate (planted) | 0.7778 |
| _(draft verification note)_ | Below 1.0 on planted loans because the LN-1042 fixture deliberately drafts the false claim 'declared income below 20000' (planted trap); the verifier rejects it, so not every drafted claim reaches VERIFIED. |
| LN-1042 false low-income rejected | True |
| LN-1042 no unsupported shipped | True |
| LN-1230 counterfactual OK | True |
| Planted recorded-reason mismatches flagged | 1/1 |
| Recorded-reason false flags (other 3 planted) | 0 |
| Scout intent routing | 12/12 |
| Median latency (ms) | 35.91 |
| LLM call count (total) | 31 |

