# Architecture

```
Analyst UI (web) ──► Agent API (Python, FastAPI)
                        │
        ┌───────────────┼───────────────────────────┐
        ▼               ▼                           ▼
   Planner/Investigator  Evidence Ledger (SQLite)   Verifier
   (Nemotron via         claim -> query_id ->       re-runs every cited SQL
    Meterless/NVIDIA)    rows -> sha256             via Zetaris, compares hash
        │                                           │
        └──────────► Zetaris client (REST /sql-editor/sqls/run) ◄─┘
                          │
              Zetaris (Docker, local): Lightning DB over
              - applications  (CSV)
              - payments      (Parquet)
              - bureau        (REST table, local mock API)
              - policy_rules  (table; policy text in markdown chunks)
```

## Components
- **llm.py:** OpenAI-compatible client. `MODEL_BASE_URL`, `MODEL_API_KEY`, `NEMOTRON_MODEL` from `.env`. Works for Meterless or build.nvidia.com. Retries and rate-limit backoff for the free tier.
- **zetaris.py:** `run_sql(sql) -> rows`; one statement at a time; read-only guard (only SELECT/SHOW allowed from the agent); logs every query with id, timestamp.
- **ledger.py:** append-only evidence store. A claim is valid only if it has at least one query_id whose rows contain the stated values.
- **agent.py:** loop (max 8 steps): plan -> pick tool (`sql`, `policy_lookup`, `peer_cases`, `counterfactual`) -> observe -> draft claims -> stop when every claim has evidence or budget ends.
- **verifier.py:** deterministic. Re-runs cited SQL, recomputes hash, checks numbers named in the claim text appear in the rows. Marks claim VERIFIED / UNSUPPORTED. Unsupported claims are removed from the final card and shown in a "rejected" panel (this is the demo's trap moment).
- **counterfactual.py:** SQL-driven: vary one input feature at a time inside the scorecard in Zetaris SQL and report the smallest change that flips the outcome.
- **card.py:** renders Why-Card (JSON + HTML + applicant letter).
- **ui/:** single page: question box, live step trace, Why-Card, evidence drawer.
- **Meterless depth (optional, time permitting):** use Meterless H-MEM to remember past explained cases (precedent lookup). Core path must not depend on it. Confirm what the hackathon credit exposes before committing.

## Data flow guarantees
1. The model never sees raw tables it did not query; everything it knows came through a logged Zetaris query.
2. The final card contains only VERIFIED claims.
3. The agent has no write access (read-only guard + a Zetaris user with SELECT only, if available).

## Fallback modes (labelled in UI: Live / Replay)
- Live: Zetaris + Nemotron up.
- Replay: recorded trace JSON so the demo and judges can run without Docker. Banner says "Replay (recorded from a Live run)". Never presented as live.
