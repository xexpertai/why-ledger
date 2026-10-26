# Eval results (2026-10-23T12:07:28.432037+00:00)

**LIVE Zetaris JDBC + recorded STUB LLM (synthetic Why-Ledger data).**

- Zetaris backend: **ZETARIS-JDBC**
- Model backend: **STUB-recorded-nemotron**
- Eval mode: **live** (is_live=True)

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
| Scout intent routing | 12/12 |
| Median latency (ms) | 1102.47 |
| LLM call count (total) | 31 |

