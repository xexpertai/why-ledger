# Why-Ledger: HackOS submission field text
Exact HackOS field names are not in the KB, so generic headings are used. Submit by hand (Harsha). Placeholders in [brackets] are links that exist only on submit day.

## Title
Why-Ledger

## Tagline
An agent that explains loan decisions and checks every claim against the data.

## Track
Track 2: The Agent That Can Explain Why

## Description
Why-Ledger explains SYNTHETIC small-business loan decisions for a fictional lender, Harbor Credit. Asked about a loan (for example LN-1042, LN-1230, LN-1301), it gathers evidence with read-only SQL through Zetaris, logs every query with its rows and a SHA-256 hash in an append-only ledger, and has NVIDIA Nemotron draft short claims that cite their queries. A deterministic verifier (no LLM) then re-runs each cited query, compares row hashes and checks that each stated value matches its named column in the rows. Only VERIFIED claims reach the Why-Card; unmatched drafts appear in a rejected panel. The card also shows drivers ranked from scorecard points in the data (not by the LLM), a counterfactual for declines (for example LN-1230: reduce the amount by 5% flips to approve), and an illustrative applicant letter that lists the principal reasons in rank order from logged, hash-checked query evidence (not legal advice). A deterministic recorded-reason check also compares the reason code on the decision record with the data and raises a red flag when it is not supported.

Results, primary run (eval/results-live-nightshift.md; the reason-check line is from card.reason_check in replay/LN-*.live-nightshift-planned.json; recorded 25 Oct 2026 17:0x UTC, model nvidia/nemotron-3.5-lightning-30b-a3b via Meterless Nightshift, 4 planted loans, one live run each): the top driver matched the planted truth 4/4. The model drafted 22 claims (LN-1042 6, LN-1107 5, LN-1230 6, LN-1301 5); all 22 were verified and none rejected. Latency was 6.7 s to 10.2 s per loan in this recording (an earlier recording the same day, with the free-tier model under load, took 67 s to 534 s). The recorded-reason check flagged the one planted mismatch (LN-1301: record says INCOME_BELOW_THRESHOLD, data shows declared income 110000 against a 40000 threshold) and raised 0 false flags on the other three loans. Scout intent routing scored 12/12 on 12 author-written cases (eval/results-live-zetaris.md, live Zetaris, recorded stub model).

Verifier stress test (eval/verifier-stress.md): PLANTED test claims, not model output. 14 cases on live Zetaris: 4 true claims VERIFIED, 10 tampered claims UNSUPPORTED (wrong value, value under the wrong column, non-existent column, wrong loan scope, bad SQL); verdict matched 14/14 and the intended failure reason matched 14/14 (reported separately).

Honest failure: the first Nightshift attempt on LN-1230 crashed the pipeline because a drafted claim cited a non-existent column (bureau_score is a display alias; the real column is score). The verifier now rejects any claim whose SQL fails on Zetaris instead of crashing, and the run was re-recorded. An earlier run (23 Oct, NVIDIA API route, eval/results-live-planned.md) drafted 33 claims, verified 31, rejected 2.

Limits: one run per loan, so no variance claims; the demo uses a labelled "Recorded live run" replay for repeatability (live latency varies on the free-tier model); data is synthetic; not legal or compliance advice.

## How it uses sponsor tech
- Zetaris: the only data path. Live JDBC (src/whyledger/zetaris_jdbc.py, ZETARIS_LIVE=jdbc) to SYNTHETIC tables in Zetaris 2.4.3.1 running locally in Docker (WHY_LEDGER_SYN.*); agent evidence queries and verifier re-runs are read-only SQL.
- Meterless Scout Intent engine: every question passes Sense, Guard, Route, Contract before the agent runs (src/whyledger/intent.py, wired first in src/whyledger/agent.py); risky or injected prompts are refused before the LLM; tested against a port of the upstream adversarial set (docs/METERLESS-ENGINE.md).
- Meterless Nightshift (primary provider): get_llm() in src/whyledger/llm.py selects Nightshift when METERLESS_RUNTIME_TOKEN is set. Nightshift 1.2.3 runs on the Windows host; WSL reaches it through a local portproxy. The NVIDIA key is configured in Nightshift under Setup > Cloud providers (shown in the video, key hidden). The four recorded runs (eval/results-live-nightshift.md, replay/LN-*.live-nightshift-planned.json) record provider meterless-nightshift in the trace; UI banner: "Zetaris live + Nemotron via Meterless Nightshift".
- NVIDIA Nemotron: nvidia/nemotron-3.5-lightning-30b-a3b via Nightshift drafts claims from the evidence table (1 call per loan in planned mode). On this account nemotron-3-super-120b-a12b returned 404 and llama-3.3-nemotron-super-49b returned 410.
- Cursor: all production code (src/, scripts/, data/, UI) was written by cursor-agent (C0-C3 and C1b with Cursor Auto, C4 onward with composer-2.5); log in docs/CURSOR-LOG.md.

## Repo
https://github.com/xexpertai/why-ledger

## Video
https://youtu.be/IbN5xGJQZ7U

## Slide deck
https://github.com/xexpertai/why-ledger/blob/main/submission/Why-Ledger-deck.pdf (11-slide PDF; the same file can be uploaded to the form directly)

## Disclosure
Entrant: Harsha Shinde (solo). Production code was written by Cursor (cursor-agent; composer-2.5, plus Cursor Auto for the first tasks), directed and reviewed by the entrant with Claude Code (Anthropic), which also did design, specs, prompts, docs, test review and video QA. Video narration: AI text-to-speech voice (Kokoro). All data, Harbor Credit, applicants and loan IDs are fictional and labelled SYNTHETIC. Repo started 22 Oct 2026; no pre-existing components. Numbers come from recorded runs in eval/. The reason-check and letter fields in the recorded bundles were regenerated offline and deterministically from the stored evidence rows of that run (no new model call). The demo video uses a replay of a recorded live run, labelled in the UI.
