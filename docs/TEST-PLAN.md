# Test plan
- **Unit:** SELECT-only guard; hash and number-in-rows verifier; scorecard maths; letter template (no banned phrases such as "legally").
- **Data:** planted facts exist exactly as in truth.json; generator deterministic (two runs same checksum).
- **Agent (fixture):** recorded Nemotron responses replayed; asserts top driver, claim count, no unsupported claim shipped.
- **Adversarial:** inject a false claim (income too low) -> must land in `rejected`; prompt-injection text inside a policy_docs chunk must not change tool use.
- **Integration (Live, once):** Zetaris `SELECT 1`, load sources, run LN-1042 end to end; record trace for Replay.
- **UI:** Playwright smoke: ask LN-1042, card shows, evidence drawer opens, badges present.
- **Eval:** 4 planted + 6 random loans; report driver accuracy, verified-claim rate, median latency, Nemotron calls. Publish only measured numbers.
- **Resource:** run Zetaris alone under `flock ~/workspace/.heavy.lock systemd-run --user --scope -p MemoryMax=8G`; check `free -g` first.
- **Release:** fresh clone, follow README, tests pass; secret scan (grep for nvapi-, bearer strings) before any public flip.
