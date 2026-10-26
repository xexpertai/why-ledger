# Live swap (env flags)

Why-Ledger defaults to **STUB** backends (sqlite + recorded Nemotron fixtures) so tests and `make eval` need no Docker or API keys. Flip to live Zetaris and **Meterless Nightshift** (primary LLM when `METERLESS_RUNTIME_TOKEN` is set) with env flags only.

## Zetaris JDBC (`ZETARIS_LIVE=jdbc`)

Local Docker platform (port 10000). Install optional deps: `.venv/bin/pip install -e '.[live]'`. Password is read from `ZETARIS_JDBC_PASSWORD` or `ZETARIS_PLATFORM_ENV` (never logged).

| Variable | Default | Purpose |
|----------|---------|---------|
| `ZETARIS_LIVE` | unset | Set to `jdbc` for `JdbcZetarisClient` |
| `ZETARIS_JDBC_URL` | `jdbc:zetaris:lightning@localhost:10000` | Lightning JDBC URL |
| `ZETARIS_JDBC_USER` | `admin@zetaris.com` | Login user |

`GET /health` reports `zetaris.backend` as `ZETARIS-JDBC`. Load SYNTHETIC tables:

```bash
make data
ZETARIS_LIVE=jdbc .venv/bin/python scripts/load_zetaris.py --apply
```

Live integration test: `ZETARIS_LIVE=jdbc .venv/bin/pytest tests/integration/test_zetaris_live.py -m live`.

## Zetaris REST (`ZETARIS_LIVE=1`)

Per the participant guide (Data 1.9):

| Variable | Default | Purpose |
|----------|---------|---------|
| `ZETARIS_LIVE` | unset | Set to `1` to use REST instead of STUB sqlite |
| `ZETARIS_REST_URL` | `http://localhost:8888/api/v1.0` | Runtime base URL |
| `ZETARIS_ORG_ID` | `1` | `X-Org-ID` header |
| `ZETARIS_API_KEY` | (required when live) | Bearer token; never log or commit |

`GET /health` reports `zetaris.backend` as `Rest` or `STUB` and probes live REST with `SELECT 1`.

Register demo tables (dry-run first):

```bash
make data
.venv/bin/python scripts/load_zetaris.py          # print Lightning SQL
ZETARIS_LIVE=1 .venv/bin/python scripts/load_zetaris.py --apply   # one statement at a time
```

## Meterless Nightshift (`METERLESS_RUNTIME_TOKEN`) — primary LLM

Nightshift **1.2.3** runs on the **Windows host** for this hackathon setup. WSL2 is a separate network namespace: `127.0.0.1` inside WSL is not the Windows host.

1. Install and start Meterless Nightshift on Windows (Console shows Runtime URL + token).
2. Expose the runtime to WSL (example: Windows `portproxy` from host `18088` → engine `127.0.0.1:8088`).
3. From WSL, point at the host (example):

   ```bash
   export METERLESS_BASE_URL=http://172.22.48.1:18088/v1
   export METERLESS_RUNTIME_TOKEN=<paste from Nightshift Console>
   export METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
   ```

4. Test (prints only pass/fail, model id, and count of `nvidia/*` models listed — no secrets):

   ```bash
   .venv/bin/python scripts/test_connection.py
   ```

**Models on this account (honest):**

| Model id | Result |
|----------|--------|
| `nvidia/nemotron-3.5-lightning-30b-a3b` | Works (used for recorded runs) |
| `nvidia/nemotron-3-super-120b-a12b` | HTTP **404** |
| `llama-3.3-nemotron-super-49b` | HTTP **410** |

`get_llm()` priority: **`LLM_ROUTE`** override (`nvidia` | `meterless` | `stub`) → **Meterless** (`METERLESS_RUNTIME_TOKEN`) → **NVIDIA direct** (`NVIDIA_API_KEY`) → **STUB fixture**. Investigator trace rows from live LLM steps include `provider: meterless-nightshift` and the response `model` id.

NVIDIA direct fallback (integrate API):

```bash
export LLM_ROUTE=nvidia   # optional; forces NVIDIA even if Meterless token is set
export NVIDIA_API_KEY=<nvapi-key>
NEMOTRON_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
```

`NEMOTRON_HTTP_TIMEOUT` applies to both NVIDIA direct and Meterless Nightshift chat calls.

## Live agent mode (`LIVE_AGENT_MODE`)

| Value | Behaviour |
|-------|-----------|
| `planned` (default when NVIDIA or Meterless is live) | Code runs a fixed read-only evidence plan per loan; **one** Nemotron call drafts `draft_claims` (≤2 calls with one JSON repair). Verifier unchanged. |
| `loop` | Legacy tool-step investigator (`plan` → `sql` / `policy_lookup` / `peer_cases` → `draft_claims`), max 4 LLM calls. |

Planned eval (four loans, writes `replay/LN-*.live-nightshift-planned.json` when Nightshift is primary):

```bash
ZETARIS_LIVE=jdbc LIVE_AGENT_MODE=planned .venv/bin/python scripts/live_nemotron_planned_eval.py
```

## Full live stack

```bash
export ZETARIS_LIVE=jdbc
export METERLESS_RUNTIME_TOKEN=<nightshift-token>
export METERLESS_BASE_URL=http://<windows-host-ip>:18088/v1
export METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
make run
```

Cards and `/health` show `mode: live` when JDBC/REST Zetaris and either Meterless or NVIDIA model credentials are configured.
