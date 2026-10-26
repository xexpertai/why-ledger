# Spec (v0.1)

## API
- `POST /ask {question|loan_id}` -> stream of steps (SSE) then `WhyCard`.
- `GET /card/{id}`; `GET /evidence/{claim_id}` (query, rows, hash, verification result).
- `GET /health` (zetaris ok, model ok, mode live|replay).

## WhyCard (JSON)
`{decision, mode, drivers:[{rank, feature, weight_pct, claim_id}], claims:[{id, text, status, query_ids, row_hash}], rejected:[{text, reason}], counterfactual:{change, flips_to}, applicant_letter, verified_at, model, synthetic:true}`

## Acceptance criteria
1. For all 4 planted loans, top driver matches `truth.json`.
2. 100% of claims in the final card are VERIFIED; 0 unsupported claim ships.
3. A deliberately wrong claim injected into the draft is caught by the verifier (test).
4. Agent cannot run non-SELECT SQL (test).
5. Card for LN-1042 produced in under 90 s on Live mode.
6. Replay mode runs with no Docker and no keys.
7. UI shows mode badge (Live/Replay) and SYNTHETIC badge.

## Out of scope
Real lender integration, legal compliance claims, auth, multi-tenant.
