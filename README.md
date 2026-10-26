# Why-Ledger

**Open Agent Hackathon 2026, Track 2 — The Agent That Can Explain Why**  
Entrant: **Harsha Shinde** (solo).  
Demo video: https://youtu.be/IbN5xGJQZ7U

Why-Ledger is an agent that explains **synthetic** small-business loan decisions for the fictional lender **Harbor Credit**. Every fact on the Why-Card is tied to a logged Zetaris SQL query and re-checked by a deterministic verifier before it is shown. Drivers on the card are ranked from scorecard points in the data, not from the LLM.

All demo data is **SYNTHETIC** and labelled as such in the UI and API (`synthetic: true`).

## How it works

1. **Scout Intent** (Meterless) classifies the user question and routes to safe tools before the investigator runs.
2. **Investigator** gathers evidence via read-only SQL on Zetaris (live **planned** mode: fixed evidence plan in code; **loop** mode: model proposes SQL steps).
3. **Evidence ledger** stores each query, columns, rows, and SHA-256 hash (append-only).
4. **Draft claims** — Nemotron proposes short factual claims with cited SQL (planned mode: one short LLM pass over the evidence table).
5. **Verifier** re-runs every cited SELECT, compares row hashes, and checks that stated values appear in rows. Unsupported drafts go to the **rejected** panel; only **VERIFIED** claims ship on the card.
6. **Recorded-reason check** — deterministic: compares the reason code stored on the decision record with the policy rule and the data (SQL-backed, evidence logged). LN-1301 records INCOME_BELOW_THRESHOLD but declared income is 110,000 against a 40,000 threshold, so the card shows a red "Recorded reason not supported by data" flag.
7. **Why-Card** — decision, drivers, claims, optional counterfactual (declines), applicant letter (illustrative ranked principal reasons, not legal advice).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a component diagram.

## Architecture (sponsor stack)

| Layer | Role |
|--------|------|
| **Zetaris (JDBC)** | Sole data path — Lightning DB over SYNTHETIC CSV/Parquet (`WHY_LEDGER_SYN.*`). Set `ZETARIS_LIVE=jdbc` for live Docker/JDBC. |
| **Meterless Scout Intent** | Intent routing and guard before the agent loop ([docs/METERLESS-ENGINE.md](docs/METERLESS-ENGINE.md)). |
| **Meterless Nightshift** | **Primary** live LLM when `METERLESS_RUNTIME_TOKEN` is set (OpenAI-compatible runtime on Windows; WSL uses host IP + portproxy). |
| **NVIDIA Nemotron** | Planning/drafting via Nightshift (`METERLESS_MODEL`) or fallback `LLM_ROUTE=nvidia` (integrate.api.nvidia.com). |
| **Verifier** | Python, no LLM — hash + value checks on every claim. |

## Run locally

Requirements: Python 3.12+, project venv, Docker (for live Zetaris per organiser bundle).

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env   # set ZETARIS_* and model keys locally; never commit .env
make data && make load
make run               # API + UI at http://127.0.0.1:8090
```

**Modes**

| Mode | How |
|------|-----|
| **Replay** | `WHYLEDGER_MODE=replay` or `make replay` — recorded traces under `replay/`, no Docker/keys. UI shows Replay banner. |
| **Stub** | Default without live flags — in-memory Zetaris stub + recorded LLM fixtures. |
| **Live** | `ZETARIS_LIVE=jdbc`, `METERLESS_RUNTIME_TOKEN` (Nightshift primary) or `LLM_ROUTE=nvidia`, optional `LIVE_AGENT_MODE=planned` (default on live LLM) or `loop`. |

Smoke scripts (true live, one run each): `scripts/live_nemotron_planned_smoke.py`, `scripts/live_nemotron_planned_eval.py`.

Offline metrics: `make eval` → [eval/results.md](eval/results.md) (STUB fixtures).

## Eval results (honest numbers)

| Report | What it measures |
|--------|------------------|
| [eval/results.md](eval/results.md) | Offline STUB: planted loans + sample, verifier trap on LN-1042, scout intent, counterfactual LN-1230. |
| [eval/results-live-zetaris.md](eval/results-live-zetaris.md) | Live Zetaris JDBC + STUB LLM on planted loans. |
| [eval/live-nemotron-smoke.md](eval/live-nemotron-smoke.md) | One live Nemotron **loop** run on LN-1042 (tool-step agent; weak draft on that run). |
| [eval/live-nemotron-planned.md](eval/live-nemotron-planned.md) | One live Nemotron **planned** run on LN-1042 after scorecard/bureau labelling fix. |
| [eval/results-live-planned.md](eval/results-live-planned.md) | Four planted loans, NVIDIA-direct planned runs (`LLM_ROUTE=nvidia`; secondary replay bundles). |
| [eval/results-live-nightshift.md](eval/results-live-nightshift.md) | Four planted loans, one live planned run each via **Meterless Nightshift** (`replay/LN-*.live-nightshift-planned.json`). |

Ground truth for tests only: `data/truth.json` (not exposed to the agent).

## How Nightshift is used

- **Primary provider:** `get_llm()` picks Meterless Nightshift when `METERLESS_RUNTIME_TOKEN` is set (over NVIDIA direct unless `LLM_ROUTE=nvidia` forces integrate API).
- **Host layout:** Nightshift runs on Windows; from WSL set `METERLESS_BASE_URL` to the Windows host IP and portproxy (for example `http://172.22.48.1:18088/v1`), not `127.0.0.1:8088` inside WSL.
- **Model:** `nvidia/nemotron-3.5-lightning-30b-a3b via Meterless Nightshift` (`METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b` on this account). `nvidia/nemotron-3-super-120b-a12b` returns **404**; `llama-3.3-nemotron-super-49b` returns **410**.
- **Demo UI:** “Recorded live run” defaults to **Nightshift run (25 Oct, primary)** (`replay/LN-*.live-nightshift-planned.json`). **NVIDIA-direct run (23 Oct, earlier)** replays `*.live-nemotron-planned.json`. **Verifier stress test (planted claims)** shows `eval/verifier-stress.md` (PLANTED test claims, not model output).

## Results (recorded Nightshift run, 25 Oct 2026, SYNTHETIC)

- 4 planted loans, one run each: 22 claims drafted, 22 verified, 0 rejected; top driver matched truth 4/4; latency 6.7 s to 10.2 s per loan (an earlier recording the same day took 67 s to 534 s on the shared free-tier model).
- Recorded-reason check: 1/1 planted mismatch flagged (LN-1301), 0 false flags on the other 3 (source: `card.reason_check` in `replay/LN-*.live-nightshift-planned.json`, and `eval/results.md` for the stub run; it is not in `eval/results-live-nightshift.md`).
- Verifier stress test (planted claims, not model output): 14 cases, 4 VERIFIED, 10 UNSUPPORTED; verdict matched 14/14, intended reason matched 14/14.

## Slide deck

`submission/Why-Ledger-deck.pdf`, built by `submission/deck/build_deck.py` from the real UI screenshots in `docs/screenshots/`.

## Limits

- SYNTHETIC data only; not legal or compliance advice.
- Free-tier / rate limits on Nemotron and Zetaris; live runs can time out or vary run-to-run.
- Planned mode caps LLM draft calls at 2; loop mode caps at 4 steps.
- Peer cases and policy doc search are demo breadth, not production policy engines.

## AI disclosure

| Role | Tool |
|------|------|
| Production code (`src/`, `scripts/`, `data/`, UI) | **Cursor** (`cursor-agent`, model **composer-2.5**), directed and reviewed by the entrant; session log in [docs/CURSOR-LOG.md](docs/CURSOR-LOG.md). |
| Design, specs, prompts, test review, docs, video/QA | **Claude Code** (Anthropic). |
| Demo video narration | AI text-to-speech voice (Kokoro, af_heart). |
| Accountability | **Harsha Shinde** — all submission claims verified against runs in this repo. |

The `card.reason_check` and applicant-letter fields in the recorded bundles were regenerated offline and deterministically from the stored evidence rows of the recorded run (no new model call, no network); the model output itself is unchanged.

Harbor Credit, applicants, and loan IDs are **fictional**. Eval and replay files are labelled **SYNTHETIC**; numbers in markdown reports come from recorded runs, not invented.

## Pre-existing components

None — repo started 22 Oct 2026 in the build window. Dependencies are listed in `pyproject.toml`.
